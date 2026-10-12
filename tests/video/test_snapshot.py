from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.core.commands.process_video_task import ProcessVideoTask
from app.core.common.video.ports import SnapshotFound, SnapshotGone, SnapshotMissing
from app.core.common.video.terminal import failed_callback
from tests.video.helpers import FakeClock, FakeMedia, FakeVideoAnalyzer, drain, repository, submit, video_request

DAY = 86_400


@pytest.mark.asyncio
async def test_snapshot_matches_stored_terminal_callback(tmp_path: Path) -> None:
    clock = FakeClock()
    repo = repository(tmp_path, clock)
    job_id = submit(repo, tmp_path)
    processing = repo.snapshot(job_id)
    assert isinstance(processing, SnapshotFound)
    wire = processing.snapshot.wire()
    assert wire["status"] == "processing"
    assert (wire["finishedAt"], wire["result"], wire["error"], wire["callbackStatus"]) == (None, None, None, "pending")
    assert wire["createdAt"].endswith("Z")
    clock.advance(30)
    await drain(ProcessVideoTask(repo, FakeMedia(), FakeVideoAnalyzer(), {"io": 2, "cpu": 1}).run_once)
    done = repo.snapshot(job_id)
    assert isinstance(done, SnapshotFound)
    wire = done.snapshot.wire()
    with sqlite3.connect(repo.path) as connection:
        stored = json.loads(connection.execute("SELECT body FROM video_results").fetchone()[0])
    assert wire["result"] == stored
    assert wire["status"] == "completed"
    assert wire["error"] is None
    assert "fileUrl" not in json.dumps(wire)  # no URLs ...
    assert "artifacts" not in json.dumps(wire)  # ... or local paths


@pytest.mark.asyncio
async def test_failed_wrapper_keeps_failure_callback(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    job_id = submit(repo, tmp_path)
    for _ in range(4):
        task = repo.claim_stage({"io": 1, "cpu": 1}, 60)
        assert task is not None
        repo.block_stage(task, "INTERNAL_ERROR")
    finalize = repo.claim_stage({"io": 1}, 60)
    assert finalize is not None
    assert repo.terminalize(finalize, failed_callback(job_id, video_request()))
    outcome = repo.snapshot(job_id)
    assert isinstance(outcome, SnapshotFound)
    wire = outcome.snapshot.wire()
    assert wire["status"] == "failed"
    assert wire["result"] is not None
    assert wire["result"]["status"] == "failed"
    assert wire["result"]["result"] is None
    assert wire["error"] == wire["result"]["error"]


@pytest.mark.asyncio
async def test_unknown_vs_logically_expired_without_sweeper(tmp_path: Path) -> None:
    clock = FakeClock()
    repo = repository(tmp_path, clock)
    job_id = submit(repo, tmp_path)
    await drain(ProcessVideoTask(repo, FakeMedia(), FakeVideoAnalyzer(), {"io": 2, "cpu": 1}).run_once)
    assert isinstance(repo.snapshot("unknown"), SnapshotMissing)
    clock.advance(90 * DAY - 1)
    assert isinstance(repo.snapshot(job_id), SnapshotFound)
    clock.advance(1)  # exactly at expiry: logically expired although no cleanup ran
    assert isinstance(repo.snapshot(job_id), SnapshotGone)
    assert repo.job_row(job_id) is not None  # still physically present
    clock.advance(90 * DAY)
    assert isinstance(repo.snapshot(job_id), SnapshotMissing)
