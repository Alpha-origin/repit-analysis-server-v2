from __future__ import annotations

import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.commands.cleanup_video_jobs import CleanupVideoJobs
from app.core.commands.process_video_task import ProcessVideoTask
from app.core.common.video.dto import AnalysisOutcome, PreparedVideo
from app.core.common.video.errors import VideoStageError
from app.core.common.video.identity import IDENTITY_BUILDERS, request_fingerprint, request_identity
from app.core.common.video.policy import VideoLimits
from app.core.common.video.ports import ReplayGoneError, RepositoryUnavailableError, VideoResourceUnavailableError
from app.main.video_config import AnalyzerSettings
from app.main.video_worker import WorkerBootError, build_worker
from app.outbound.adapters.video.sqlite_repository import SCHEMA_VERSION, SqliteVideoRepository
from tests.video.helpers import (
    SOURCE_HOST,
    VIDEO_TOKEN,
    FakeClock,
    FakeMedia,
    FakeVideoAnalyzer,
    admit_for,
    callback_settings,
    drain,
    repository,
    submit,
    video_request,
    video_settings,
)
from tests.video.test_api import app_for


def test_parallel_disk_reservations_and_lease_expiry(tmp_path: Path) -> None:
    clock = FakeClock()
    repo = SqliteVideoRepository(tmp_path / "video.sqlite3", tmp_path / "artifacts", clock, disk_budget_bytes=6)
    for index in range(2):
        submit(repo, tmp_path, video_request(requestId=f"req-{index}"))
    tasks = [repo.claim_stage({"io": 2}, 60) for _ in range(2)]
    assert all(tasks)

    def reserve(index: int) -> Any:
        task = tasks[index]
        assert task is not None
        try:
            return repo.reserve_artifact(task, "source")
        except VideoResourceUnavailableError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        reservations = list(pool.map(reserve, range(2)))
    assert sum(item is not None for item in reservations) == 1
    winner = next(item for item in reservations if item is not None)
    Path(winner.temp_path).write_bytes(b"part")
    assert repo.operational_metrics()["video_disk_reserved_bytes"] == 6
    clock.advance(63)
    # A crashed/stale writer's reservation remains; expiry does not create duplicate disk capacity.
    replacement = repo.claim_stage({"io": 2}, 60)
    assert replacement is not None
    with pytest.raises(VideoResourceUnavailableError):
        repo.reserve_artifact(replacement, "source")


@pytest.mark.asyncio
async def test_disk_wait_does_not_spend_attempts_and_gc_unblocks(tmp_path: Path) -> None:
    clock = FakeClock()
    repo = SqliteVideoRepository(tmp_path / "video.sqlite3", tmp_path / "artifacts", clock, disk_budget_bytes=6)
    first = submit(repo, tmp_path, video_request(requestId="first"))
    worker = ProcessVideoTask(repo, FakeMedia(), FakeVideoAnalyzer(), {"io": 2, "cpu": 1})
    await drain(worker.run_once)
    second = submit(repo, tmp_path, video_request(requestId="second"))
    assert await worker.run_once()
    assert repo.task_rows(second)["source"]["attempts"] == 0
    assert repo.task_rows(second)["source"]["status"] == "pending"
    clock.advance(24 * 3600)
    assert CleanupVideoJobs(repo).run_once().collected_artifacts == 1
    await drain(worker.run_once)
    assert repo.job_row(first)["status"] == "completed"  # type: ignore[index]
    assert repo.job_row(second)["status"] == "completed"  # type: ignore[index]
    assert repo.task_rows(second)["source"]["attempts"] == 1


@pytest.mark.asyncio
async def test_disk_wait_deadline_converges_without_releasing_stale_writer(tmp_path: Path) -> None:
    clock = FakeClock()
    repo = SqliteVideoRepository(tmp_path / "video.sqlite3", tmp_path / "artifacts", clock, disk_budget_bytes=6)
    job = submit(repo, tmp_path)
    original = repo.claim_stage({"io": 1}, 60)
    assert original is not None
    reservation = repo.reserve_artifact(original, "source")
    assert reservation is not None
    Path(reservation.temp_path).write_bytes(b"part")
    # Simulate a crashed writer leaving its files. Reclaiming its lease must not free its disk budget.
    clock.advance(901)
    worker = ProcessVideoTask(repo, FakeMedia(), FakeVideoAnalyzer(), {"io": 1, "cpu": 1})
    await drain(worker.run_once)
    assert repo.job_row(job)["status"] == "completed"  # type: ignore[index]
    assert repo.operational_metrics()["video_disk_reserved_bytes"] == 6
    with sqlite3.connect(repo.path) as connection:
        body = json.loads(connection.execute("SELECT body FROM video_results").fetchone()[0])
    assert body["result"]["error"]["code"] == "PROCESSING_TIMEOUT"


@pytest.mark.asyncio
async def test_failed_download_releases_empty_reservation(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    submit(repo, tmp_path)
    worker = ProcessVideoTask(
        repo, FakeMedia(fail={"source": VideoStageError("SOURCE_NOT_FOUND")}), FakeVideoAnalyzer(), {"io": 1}
    )
    await drain(worker.run_once)
    assert repo.operational_metrics()["video_disk_reserved_bytes"] == 0


def test_real_v1_database_migrates_and_preserves_jobs(tmp_path: Path) -> None:
    db = tmp_path / "video.sqlite3"
    schema = (Path(__file__).parent / "fixtures/schema-v1.sql").read_text()
    with sqlite3.connect(db) as connection:
        connection.executescript(schema)
    repo = repository(tmp_path)
    job_id = submit(repo, tmp_path)
    with sqlite3.connect(db) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert repository(tmp_path).job_row(job_id) is not None
    # Policy-v1 accepted before this change must still deserialize.
    limits = VideoLimits.model_validate({"max_bytes": 6})
    assert limits.analyze_timeout_seconds == 900


def test_v1_migration_restores_existing_disk_budget_and_policy(tmp_path: Path) -> None:
    db = tmp_path / "video.sqlite3"
    schema = (Path(__file__).parent / "fixtures/schema-v1.sql").read_text()
    request = video_request()
    policy = video_settings(tmp_path).accepted_policy(()).model_dump(mode="json")
    del policy["limits"]["analyze_timeout_seconds"]
    del policy["limits"]["resource_wait_timeout_seconds"]
    directory = tmp_path / "artifacts" / "legacy" / "source"
    directory.mkdir(parents=True)
    source = directory / "token.source"
    source.write_bytes(b"video!")
    with sqlite3.connect(db) as connection:
        connection.executescript(schema)
        connection.execute(
            "INSERT INTO video_jobs (id, session_id, request_id, identity_version, fingerprint, request, policy, "
            "status, created_at) VALUES ('legacy', ?, ?, 'video-identity-v1', ?, ?, ?, 'processing', 0)",
            (
                request.session_id,
                request.request_id,
                request_fingerprint(request),
                request.model_dump_json(by_alias=True),
                json.dumps(policy),
            ),
        )
        connection.execute(
            "INSERT INTO video_artifacts (id, job_id, stage, lease_token, temp_path, final_path, state, created_at) "
            "VALUES ('artifact', 'legacy', 'source', 'token', ?, ?, 'published', 0)",
            (str(source) + ".partial", str(source)),
        )
    repo = repository(tmp_path)
    assert repo.operational_metrics()["video_disk_reserved_bytes"] == 6
    assert repo.job_row("legacy") is not None
    with sqlite3.connect(db) as connection:
        stored = json.loads(connection.execute("SELECT policy FROM video_jobs").fetchone()[0])
    assert stored == policy
    assert source.read_bytes() == b"video!"


@pytest.mark.asyncio
async def test_replay_uses_stored_version_including_tombstones(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock()
    repo = repository(tmp_path, clock)
    request = video_request()
    job_id = submit(repo, tmp_path, request)

    def v2(value: Any) -> dict[str, object]:
        return {**request_identity(value, "video-identity-v1"), "identityVersion": "test-v2"}

    monkeypatch.setitem(IDENTITY_BUILDERS, "test-v2", v2)
    current_hash = request_fingerprint(request, "test-v2")
    assert current_hash != request_fingerprint(request)
    assert repo.submit(request, current_hash, admit_for(tmp_path)).job_id == job_id
    await drain(ProcessVideoTask(repo, FakeMedia(), FakeVideoAnalyzer(), {"io": 1, "cpu": 1}).run_once)
    clock.advance(90 * 86_400)
    with pytest.raises(ReplayGoneError):
        repo.submit(request, current_hash, admit_for(tmp_path))
    with sqlite3.connect(repo.path) as connection:
        connection.execute("UPDATE video_tombstones SET identity_version='unknown'")
    with pytest.raises(RepositoryUnavailableError):
        repo.submit(request, current_hash, admit_for(tmp_path))


@pytest.mark.parametrize("lanes", [None, ("cpu", "callback"), ("io", "cpu")])
def test_production_requires_explicit_separate_lane(tmp_path: Path, lanes: Any) -> None:
    with pytest.raises(WorkerBootError, match="explicit"):
        build_worker(
            lanes,
            callback_settings=callback_settings(ENVIRONMENT="production"),
            video_settings=video_settings(tmp_path),
            media=FakeMedia(),
        )
    assert not (tmp_path / "video.sqlite3").exists()


class SlowAnalyzer:
    def __init__(self) -> None:
        self.cancelled = False

    async def analyze(self, prepared: PreparedVideo) -> AnalysisOutcome:
        try:
            await asyncio.sleep(60)
        finally:
            self.cancelled = True
        return AnalysisOutcome(status="ready", data={})


@pytest.mark.asyncio
async def test_analysis_timeout_cancels_and_converges(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    request = video_request()
    repo.submit(
        request,
        request_fingerprint(request),
        admit_for(
            tmp_path,
            policy=VideoLimits(source_hosts=(SOURCE_HOST,), min_free_disk_bytes=0, analyze_timeout_seconds=1),
            stage_max_attempts=1,
            stage_retry_delays_seconds=(),
        ),
    )
    analyzer = SlowAnalyzer()
    await drain(ProcessVideoTask(repo, FakeMedia(), analyzer, {"io": 1, "cpu": 1}).run_once)
    with sqlite3.connect(repo.path) as connection:
        body = json.loads(connection.execute("SELECT body FROM video_results").fetchone()[0])
    assert analyzer.cancelled
    assert body["result"]["error"] is None
    assert body["result"]["analysis"]["error"]["code"] == "PROCESSING_TIMEOUT"


def test_process_analyzer_checks_model_before_database(tmp_path: Path) -> None:
    settings = video_settings(
        tmp_path,
        analyzer=AnalyzerSettings(
            backend="process",
            executable=tmp_path / "missing",
            model_path=tmp_path / "model",
            model_version="model-v1",
            data_schema_version="behavior-v1",
        ),
    )
    with pytest.raises(WorkerBootError, match="executable"):
        build_worker(("cpu",), callback_settings=callback_settings(), video_settings=settings, media=FakeMedia())
    assert not (tmp_path / "video.sqlite3").exists()


@pytest.mark.asyncio
async def test_metrics_are_authenticated_and_expose_no_identifiers(tmp_path: Path) -> None:
    app = app_for(tmp_path, metrics_enabled=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        assert (await http.get("/internal/video/metrics")).status_code == 401
        repo = repository(tmp_path)
        submit(repo, tmp_path)
        CleanupVideoJobs(repo).run_once()
        response = await http.get("/internal/video/metrics", headers={"X-Internal-Token": VIDEO_TOKEN})
    assert response.status_code == 200
    assert "video_jobs_processing 1.0" in response.text
    assert "video_cleanup_last_success_timestamp_seconds" in response.text
    for secret in ("req-1", "session-1", "X-Amz", VIDEO_TOKEN, str(tmp_path)):
        assert secret not in response.text
