from __future__ import annotations

from pathlib import Path

from app.core.common.video.ports import StageOutput
from tests.video.helpers import FakeClock, repository, submit, video_request


def test_claim_capacity_and_restart(tmp_path: Path) -> None:
    clock = FakeClock()
    repo = repository(tmp_path, clock)
    first = submit(repo, tmp_path)
    second = submit(repo, tmp_path, video_request(requestId="req-2"))
    task = repo.claim_stage({"io": 1}, 60)
    assert task is not None
    assert (task.job_id, task.stage, task.attempts) == (first, "source", 1)
    assert repo.claim_stage({"io": 1}, 60) is None  # io lane full (shared across processes via the DB)
    assert repo.claim_stage({"cpu": 1}, 60) is None  # inspect waits for its dependency
    other = repository(tmp_path, clock)  # a second worker process
    assert other.claim_stage({"io": 2}, 60) is not None
    assert repo.finish_stage(task, StageOutput(data={"path": "p", "sha256": "0" * 64, "bytes": 6}))
    inspect = other.claim_stage({"cpu": 1}, 60)
    assert inspect is not None
    assert (inspect.job_id, inspect.stage) == (first, "inspect")
    # "Restart": the claimed lease expires; the stage returns after its retry delay with attempts preserved.
    clock.advance(61)
    assert repo.claim_stage({"cpu": 1}, 60) is None  # 2s retry delay after the lost lease
    clock.advance(2)
    reclaimed = repo.claim_stage({"cpu": 1}, 60)
    assert reclaimed is not None
    assert (reclaimed.id, reclaimed.attempts) == (inspect.id, 2)
    assert repo.task_rows(first)["source"]["status"] == "succeeded"  # never re-run
    assert repo.task_rows(second)["source"]["status"] == "pending"  # its expired lease was reclaimed too
    assert repo.task_rows(second)["source"]["attempts"] == 1


def test_stale_worker_cannot_finish_or_extend_lease(tmp_path: Path) -> None:
    clock = FakeClock()
    repo = repository(tmp_path, clock)
    job_id = submit(repo, tmp_path)
    stale = repo.claim_stage({"io": 1}, 60)
    assert stale is not None
    clock.advance(61)
    clock.advance(2)
    fresh = repo.claim_stage({"io": 1}, 60)
    assert fresh is not None
    assert fresh.token != stale.token
    assert not repo.heartbeat(stale, 60)
    assert not repo.finish_stage(stale, StageOutput(data={"stale": True}))
    assert not repo.fail_stage(stale, "INTERNAL_ERROR", stage_retry=False)
    assert not repo.block_stage(stale, "INTERNAL_ERROR")
    assert repo.reserve_artifact(stale, "source") is None
    row = repo.task_rows(job_id)["source"]
    assert (row["status"], row["token"], row["result"]) == ("running", fresh.token, None)
    assert repo.heartbeat(fresh, 60)


def test_expired_last_attempt_terminates(tmp_path: Path) -> None:
    clock = FakeClock()
    repo = repository(tmp_path, clock)
    job_id = submit(repo, tmp_path)
    for _ in range(3):
        assert repo.claim_stage({"io": 1}, 60) is not None
        clock.advance(61 + 4)
    assert repo.claim_stage({"io": 1}, 60) is None  # reclaims the third expiry; no io work is ready
    rows = repo.task_rows(job_id)
    assert rows["source"]["status"] == "failed"
    assert rows["source"]["error_code"] == "PROCESSING_TIMEOUT"
    assert rows["source"]["attempts"] == 3
    blocked = repo.claim_stage({"cpu": 1}, 60)  # downstream proceeds (to be blocked), job not stuck
    assert blocked is not None
    assert blocked.stage == "inspect"
