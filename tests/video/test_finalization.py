from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from app.core.commands.process_video_task import ProcessVideoTask
from app.core.common.video.errors import VideoStageError
from app.core.common.video.ports import StageOutput, StageTask
from app.core.common.video.terminal import failed_callback
from app.outbound.adapters.video.sqlite_repository import SqliteVideoRepository
from tests.video.helpers import (
    FakeClock,
    FakeMedia,
    FakeVideoAnalyzer,
    drain,
    partial_outcome,
    repository,
    submit,
    video_request,
)


def _worker(repo: SqliteVideoRepository, media: FakeMedia | None = None, analyzer: Any = None) -> ProcessVideoTask:
    return ProcessVideoTask(repo, media or FakeMedia(), analyzer or FakeVideoAnalyzer(), {"io": 2, "cpu": 1}, 60)


def _stored(repo: SqliteVideoRepository, job_id: str) -> tuple[dict[str, Any], int]:
    with sqlite3.connect(repo.path) as connection:
        body = json.loads(connection.execute("SELECT body FROM video_results WHERE job_id=?", (job_id,)).fetchone()[0])
        outboxes = connection.execute("SELECT COUNT(*) FROM video_callbacks WHERE job_id=?", (job_id,)).fetchone()[0]
    return body, outboxes


@pytest.mark.asyncio
async def test_terminal_payload_and_outbox_commit_atomically(tmp_path: Path) -> None:
    clock = FakeClock()
    repo = repository(tmp_path, clock)
    job_id = submit(repo, tmp_path)
    await drain(_worker(repo, analyzer=FakeVideoAnalyzer(partial_outcome())).run_once)
    body, outboxes = _stored(repo, job_id)
    assert outboxes == 1
    assert body["status"] == "partial"  # a finished partial result, not in-progress and not failed
    assert body["result"]["analysis"]["status"] == "partial"
    assert body["result"]["durationMs"] == 4_000
    job = repo.job_row(job_id)
    assert job is not None
    assert job["status"] == "completed"
    assert job["finished_at"] == clock.now()
    assert job["result_expires_at"] == clock.now() + 90 * 86_400
    assert job["tombstone_expires_at"] == clock.now() + 180 * 86_400
    assert repo.callback_row(job_id)["status"] == "pending"  # type: ignore[index]


@pytest.mark.asyncio
async def test_file_unavailable_is_completed_with_file_error(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    job_id = submit(repo, tmp_path)
    await drain(_worker(repo, FakeMedia(fail={"validate": VideoStageError("VIDEO_LIMIT_EXCEEDED")})).run_once)
    body, _ = _stored(repo, job_id)
    assert body["status"] == "unavailable"
    assert body["error"] is None
    assert body["result"]["error"]["code"] == "VIDEO_LIMIT_EXCEEDED"
    assert body["result"]["analysis"] == {"status": "unavailable", "data": None, "error": body["result"]["error"]}
    assert body["result"]["durationMs"] is None
    assert repo.job_row(job_id)["status"] == "completed"  # type: ignore[index]


@pytest.mark.asyncio
async def test_result_generation_failure_enqueues_minimal_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = repository(tmp_path)
    job_id = submit(repo, tmp_path)

    def broken(*args: Any) -> Any:
        raise KeyError("durationMs")

    monkeypatch.setattr("app.core.commands.process_video_task.build_terminal_callback", broken)
    await drain(_worker(repo).run_once)
    body, outboxes = _stored(repo, job_id)
    assert outboxes == 1
    assert body == failed_callback(job_id, video_request()).wire()
    assert body["result"] is None
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert repo.job_row(job_id)["status"] == "failed"  # type: ignore[index]


@pytest.mark.asyncio
async def test_finalize_exhaustion_still_converges(tmp_path: Path) -> None:
    clock = FakeClock()
    repo = repository(tmp_path, clock)
    job_id = submit(repo, tmp_path)
    worker = _worker(repo)
    for _ in range(4):
        await worker.run_once()  # source, inspect, validate, analyze
    for _ in range(3):  # finalize workers keep dying
        assert repo.claim_stage({"io": 1}, 60) is not None
        clock.advance(61 + 4)
    repo.claim_stage({"io": 1}, 60)
    body, outboxes = _stored(repo, job_id)
    assert (body["status"], outboxes) == ("failed", 1)
    assert repo.job_row(job_id)["status"] == "failed"  # type: ignore[index]


def test_crash_and_stale_terminalization_are_idempotent(tmp_path: Path) -> None:
    clock = FakeClock()
    repo = repository(tmp_path, clock)
    job_id = submit(repo, tmp_path)
    for stage in ("source", "inspect", "validate", "analyze"):
        task = repo.claim_stage({"io": 1, "cpu": 1}, 60)
        assert task is not None
        assert task.stage == stage
        repo.block_stage(task, "SOURCE_NOT_FOUND")
    stale = repo.claim_stage({"io": 1}, 60)
    assert stale is not None
    assert stale.stage == "finalize"
    body = failed_callback(job_id, video_request())
    clock.advance(70)
    current = repo.claim_stage({"io": 1}, 60)
    assert current is not None
    assert not repo.terminalize(stale, body)  # stale owner writes nothing
    assert repo.terminalize(current, body)
    finished_at = repo.job_row(job_id)["finished_at"]  # type: ignore[index]
    clock.advance(10)
    assert not repo.terminalize(current, body)  # already finished: no second outbox, no new finishedAt
    _, outboxes = _stored(repo, job_id)
    assert outboxes == 1
    assert repo.job_row(job_id)["finished_at"] == finished_at  # type: ignore[index]
    forged = StageTask(id="x", job_id=job_id, stage="source", token=current.token, attempts=1)
    assert not repo.finish_stage(forged, StageOutput(data={}))
