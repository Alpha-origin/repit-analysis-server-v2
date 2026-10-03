from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.core.commands.process_video_task import ProcessVideoTask
from app.core.common.video.errors import VideoStageError
from app.outbound.adapters.video.sqlite_repository import SqliteVideoRepository
from tests.video.helpers import FakeClock, FakeMedia, FakeVideoAnalyzer, drain, repository, submit


def processor(
    repo: SqliteVideoRepository, media: FakeMedia, analyzer: FakeVideoAnalyzer, lease: int = 60
) -> ProcessVideoTask:
    return ProcessVideoTask(repo, media, analyzer, {"io": 2, "cpu": 1}, lease)


@pytest.mark.asyncio
async def test_dag_dispatch_and_committed_stage_reuse(tmp_path: Path) -> None:
    clock = FakeClock()
    repo = repository(tmp_path, clock)
    job_id = submit(repo, tmp_path)
    media = FakeMedia(fail={"validate": VideoStageError("EXTERNAL_SERVICE_UNAVAILABLE", stage_retry=True)})
    analyzer = FakeVideoAnalyzer()
    worker = processor(repo, media, analyzer)
    await drain(worker.run_once)
    assert repo.task_rows(job_id)["validate"]["status"] == "pending"  # transient: waits for retry delay
    media.fail.clear()
    clock.advance(2)
    await drain(worker.run_once)
    rows = repo.task_rows(job_id)
    assert {stage: row["status"] for stage, row in rows.items()} == dict.fromkeys(rows, "succeeded")
    # Committed stages ran exactly once; only the failed stage was retried.
    assert media.calls == {"source": 1, "inspect": 1, "validate": 2}
    assert len(analyzer.calls) == 1
    assert analyzer.calls[0].duration_ms == 4_000
    final = Path(json.loads(rows["source"]["result"])["path"])
    assert final.read_bytes() == b"video!"
    assert final.parent == tmp_path / "artifacts" / job_id / "source"
    assert not list(final.parent.glob("*.partial"))
    assert repo.job_row(job_id)["status"] == "completed"  # type: ignore[index]


@pytest.mark.asyncio
async def test_source_failure_blocks_analyzer_but_finalizes(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    job_id = submit(repo, tmp_path)
    media = FakeMedia(fail={"source": VideoStageError("SOURCE_NOT_FOUND")})
    analyzer = FakeVideoAnalyzer()
    await drain(processor(repo, media, analyzer).run_once)
    rows = repo.task_rows(job_id)
    assert rows["source"]["status"] == "failed"
    assert [rows[stage]["status"] for stage in ("inspect", "validate", "analyze")] == ["blocked"] * 3
    assert {rows[stage]["error_code"] for stage in ("inspect", "validate", "analyze")} == {"SOURCE_NOT_FOUND"}
    assert rows["finalize"]["status"] == "succeeded"
    assert analyzer.calls == []
    assert media.calls == {"source": 1}  # permanent failure: no internal retry


@pytest.mark.asyncio
async def test_unexpected_exception_is_internal_error_without_leaking(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    repo = repository(tmp_path)
    job_id = submit(repo, tmp_path)
    media = FakeMedia(fail={"inspect": RuntimeError("https://bucket/x?X-Amz-Signature=leak")})
    await drain(processor(repo, media, FakeVideoAnalyzer()).run_once)
    assert repo.task_rows(job_id)["inspect"]["error_code"] == "INTERNAL_ERROR"
    assert "leak" not in caplog.text


@pytest.mark.asyncio
async def test_lease_loss_cancels_execution(tmp_path: Path) -> None:
    clock = FakeClock()
    repo = repository(tmp_path, clock)
    job_id = submit(repo, tmp_path)
    hold = asyncio.Event()
    media = FakeMedia(hold=hold)
    worker = processor(repo, media, FakeVideoAnalyzer(), lease=3)
    assert await worker.run_once()  # source
    running = asyncio.create_task(worker.run_once())  # inspect blocks on the event
    await asyncio.sleep(0.2)
    clock.advance(10)  # lease expires; another worker takes the stage over
    thief = repository(tmp_path, clock)
    clock.advance(2)
    stolen = thief.claim_stage({"cpu": 1}, 60)
    assert stolen is not None
    assert stolen.stage == "inspect"
    assert await asyncio.wait_for(running, timeout=5) is True  # heartbeat noticed and cancelled execution
    hold.set()
    row = repo.task_rows(job_id)["inspect"]
    assert (row["status"], row["token"], row["result"]) == ("running", stolen.token, None)  # zero stale writes
