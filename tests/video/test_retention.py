from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.core.commands.cleanup_video_jobs import CleanupVideoJobs
from app.core.commands.process_video_task import ProcessVideoTask
from app.core.common.video.identity import request_fingerprint
from app.core.common.video.ports import (
    ReplayConflictError,
    ReplayGoneError,
    SnapshotFound,
    SnapshotGone,
    SnapshotMissing,
)
from app.outbound.adapters.video.sqlite_repository import SqliteVideoRepository
from tests.video.helpers import FakeClock, FakeMedia, FakeVideoAnalyzer, admit_for, drain, repository, video_request

DAY = 86_400


async def finished(tmp_path: Path, clock: FakeClock) -> tuple[SqliteVideoRepository, str]:
    repo = repository(tmp_path, clock)
    request = video_request()
    job_id = repo.submit(request, request_fingerprint(request), admit_for(tmp_path)).job_id
    await drain(ProcessVideoTask(repo, FakeMedia(), FakeVideoAnalyzer(), {"io": 2, "cpu": 1}).run_once)
    return repo, job_id


def resubmit(repo: SqliteVideoRepository, tmp_path: Path, **changes: object) -> str:
    request = video_request(**changes)
    return repo.submit(request, request_fingerprint(request), admit_for(tmp_path)).job_id


def count(repo: SqliteVideoRepository, table: str) -> int:
    with sqlite3.connect(repo.path) as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608


@pytest.mark.asyncio
async def test_exact_result_and_tombstone_windows(tmp_path: Path) -> None:
    clock = FakeClock()
    repo, job_id = await finished(tmp_path, clock)
    finished_at = clock.now()
    cleanup = CleanupVideoJobs(repo)
    clock.value = finished_at + 90 * DAY - 1
    assert cleanup.run_once().expired_jobs == 0
    assert isinstance(repo.snapshot(job_id), SnapshotFound)
    clock.value = finished_at + 90 * DAY  # now >= expiry
    stats = cleanup.run_once()
    assert stats.expired_jobs == 1
    assert isinstance(repo.snapshot(job_id), SnapshotGone)
    # Sensitive data went with the job in the same transaction; only a minimal tombstone remains.
    for table in ("video_jobs", "video_tasks", "video_results", "video_callbacks", "video_dependencies"):
        assert count(repo, table) == 0, table
    with sqlite3.connect(repo.path) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(video_tombstones)")]
        expires = connection.execute("SELECT expires_at FROM video_tombstones").fetchone()[0]
    assert "request" not in columns
    assert "body" not in columns
    assert expires == finished_at + 180 * DAY  # anchored to the original expiry, not the sweep time
    with pytest.raises(ReplayGoneError):
        resubmit(repo, tmp_path)
    clock.value = finished_at + 180 * DAY - 1
    assert cleanup.run_once().purged_tombstones == 0
    clock.value = finished_at + 180 * DAY
    assert cleanup.run_once().purged_tombstones == 1
    assert isinstance(repo.snapshot(job_id), SnapshotMissing)
    new_job = resubmit(repo, tmp_path)  # the key is free again: a brand-new job
    assert new_job != job_id


@pytest.mark.asyncio
async def test_expiry_replay_conflict_and_processing_exclusion(tmp_path: Path) -> None:
    clock = FakeClock()
    repo, job_id = await finished(tmp_path, clock)
    processing = resubmit(repo, tmp_path, requestId="still-running")
    clock.advance(90 * DAY)
    # No sweeper ran: POST applies logical expiry itself (same content 410, changed content 409).
    with pytest.raises(ReplayGoneError):
        resubmit(repo, tmp_path)
    with pytest.raises(ReplayConflictError):
        resubmit(repo, tmp_path, userId="other")
    assert repo.job_row(job_id) is None  # converted to a tombstone inside the POST transaction
    clock.advance(1000 * DAY)
    CleanupVideoJobs(repo).run_once()
    assert repo.job_row(processing) is not None  # processing jobs never expire
    assert isinstance(repo.snapshot(processing), SnapshotFound)


@pytest.mark.asyncio
async def test_expiry_racing_delivery_and_lookup(tmp_path: Path) -> None:
    clock = FakeClock()
    repo, job_id = await finished(tmp_path, clock)
    claim = repo.claim_callback(1, 60)  # an attempt started before expiry ...
    assert claim is not None
    clock.advance(90 * DAY)
    assert isinstance(repo.snapshot(job_id), SnapshotGone)
    assert repo.claim_callback(1, 60) is None  # ... and no new attempt starts at/after expiry
    CleanupVideoJobs(repo).run_once()
    assert not repo.finish_callback(claim, "retry", "http_503")  # completion cannot recreate the outbox
    assert count(repo, "video_callbacks") == 0
    assert isinstance(repo.snapshot(job_id), SnapshotGone)
