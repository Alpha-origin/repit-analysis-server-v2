from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from app.core.commands.cleanup_video_jobs import CleanupVideoJobs
from app.core.commands.process_video_task import ProcessVideoTask
from app.core.common.video.ports import SnapshotFound
from app.outbound.adapters.video.sqlite_repository import SqliteVideoRepository
from tests.video.helpers import FakeClock, FakeMedia, FakeVideoAnalyzer, drain, repository, submit, video_request

HOUR = 3_600


async def finished(repo: SqliteVideoRepository, tmp_path: Path, request_id: str = "req-1") -> tuple[str, Path]:
    job_id = submit(repo, tmp_path, video_request(requestId=request_id))
    await drain(ProcessVideoTask(repo, FakeMedia(), FakeVideoAnalyzer(), {"io": 2, "cpu": 1}).run_once)
    source = Path(json.loads(repo.task_rows(job_id)["source"]["result"])["path"])
    return job_id, source


@pytest.mark.asyncio
async def test_terminal_media_deleted_after_24h_without_result_loss(tmp_path: Path) -> None:
    clock = FakeClock()
    repo = repository(tmp_path, clock)
    job_id, source = await finished(repo, tmp_path)
    before = repo.snapshot(job_id)
    cleanup = CleanupVideoJobs(repo)
    clock.advance(24 * HOUR - 1)
    assert cleanup.run_once().collected_artifacts == 0
    assert source.exists()
    clock.advance(1)
    assert cleanup.run_once().collected_artifacts == 1
    assert not source.exists()
    assert not (tmp_path / "artifacts" / job_id).exists()  # empty job directories are removed too
    after = repo.snapshot(job_id)
    assert isinstance(before, SnapshotFound)
    assert isinstance(after, SnapshotFound)
    assert after.snapshot.result == before.snapshot.result  # GET still serves the stored body
    claim = repo.claim_callback(1, 60)
    assert claim is not None
    assert claim.body == before.snapshot.result.wire()  # type: ignore[union-attr]  # retries never read media
    assert cleanup.run_once().collected_artifacts == 0  # idempotent


@pytest.mark.asyncio
async def test_active_referenced_and_external_paths_survive(tmp_path: Path) -> None:
    clock = FakeClock()
    repo = repository(tmp_path, clock)
    _, done_source = await finished(repo, tmp_path, "done")
    # A processing job whose source is published but whose later stages are still running.
    active = submit(repo, tmp_path, video_request(requestId="active"))
    worker = ProcessVideoTask(repo, FakeMedia(), FakeVideoAnalyzer(), {"io": 1}, 60)
    assert await worker.run_once()
    active_source = Path(json.loads(repo.task_rows(active)["source"]["result"])["path"])
    # Same bytes (same checksum) as the finished job: files are never shared between jobs.
    assert active_source.read_bytes() == done_source.read_bytes()
    assert active_source != done_source
    # An outside file referenced by a tampered manifest is never touched.
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"keep me")
    with sqlite3.connect(repo.path) as connection:
        connection.execute(
            "INSERT INTO video_artifacts (id, job_id, stage, lease_token, temp_path, final_path, state, created_at)"
            " VALUES ('evil', 'gone-job', 'source', 't', ?, ?, 'published', 0)",
            (str(outside) + ".partial", str(outside)),
        )
    clock.advance(10 * 24 * HOUR)
    stats = CleanupVideoJobs(repo).run_once()
    assert stats.collected_artifacts == 1
    assert stats.artifact_errors == 1
    assert not done_source.exists()
    assert active_source.exists()
    assert outside.read_bytes() == b"keep me"


@pytest.mark.asyncio
async def test_gc_crash_and_orphan_recovery_are_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock()
    repo = repository(tmp_path, clock)
    job_id = submit(repo, tmp_path)
    task = repo.claim_stage({"io": 1}, 60)
    assert task is not None
    reservation = repo.reserve_artifact(task, "source")
    assert reservation is not None
    Path(reservation.temp_path).write_bytes(b"renamed-then-rolled-back")
    # Simulate: rename succeeded but the DB transaction failed -> final file exists, manifest still staged.
    Path(reservation.temp_path).replace(reservation.final_path)
    clock.advance(65)
    await drain(ProcessVideoTask(repo, FakeMedia(), FakeVideoAnalyzer(), {"io": 2, "cpu": 1}).run_once)
    assert repo.job_row(job_id)["status"] == "completed"  # type: ignore[index]
    orphan = Path(reservation.final_path)
    assert orphan.exists()  # processing never deletes it ...
    clock.advance(24 * HOUR)
    real_unlink = Path.unlink
    calls = {"count": 0}

    def flaky_unlink(self: Path, missing_ok: bool = False) -> None:
        calls["count"] += 1
        if calls["count"] == 1:
            raise PermissionError("simulated")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)
    first = CleanupVideoJobs(repo).run_once()
    assert first.artifact_errors == 1  # crash/permission failure: manifest kept for the next pass
    monkeypatch.setattr(Path, "unlink", real_unlink)
    second = CleanupVideoJobs(repo).run_once()
    assert first.collected_artifacts + second.collected_artifacts == 2
    assert not orphan.exists()  # ... the 24h collector recovers it from the staged manifest
    assert not any(p.is_file() for p in (tmp_path / "artifacts").rglob("*"))
    assert CleanupVideoJobs(repo).run_once().collected_artifacts == 0


def test_artifact_directories_are_private(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    submit(repo, tmp_path)
    task = repo.claim_stage({"io": 1}, 60)
    assert task is not None
    reservation = repo.reserve_artifact(task, "source")
    assert reservation is not None
    directory = Path(reservation.final_path).parent
    assert directory.stat().st_mode & 0o777 == 0o700
    assert os.path.commonpath([directory, tmp_path / "artifacts"]) == str((tmp_path / "artifacts").resolve())
