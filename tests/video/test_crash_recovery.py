"""Kill a real child process at precise points and prove the persisted state recovers correctly."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from app.core.commands.cleanup_video_jobs import CleanupVideoJobs
from app.core.commands.deliver_video_callback import DeliverVideoCallback
from app.core.commands.process_video_task import ProcessVideoTask
from app.core.common.video.identity import request_fingerprint
from app.outbound.adapters.httpx_callback_transport import HttpxCallbackTransport
from tests.video.helpers import (
    START,
    FakeClock,
    FakeMedia,
    FakeVideoAnalyzer,
    Receiver,
    admit_for,
    drain,
    repository,
    security,
    submit,
    video_request,
)

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE: list[dict[str, Any]] = []


def crash(scenario: str, directory: Path) -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "tests.video.crash_child", scenario, str(directory)],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": f"{ROOT / 'src'}{os.pathsep}{ROOT}"},
        capture_output=True,
        check=False,
    )
    assert result.returncode == 17, result.stderr.decode()[-2000:]


def counts(directory: Path) -> dict[str, int]:
    with sqlite3.connect(directory / "video.sqlite3") as connection:
        return {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
            for table in ("video_jobs", "video_tasks", "video_results", "video_callbacks", "video_artifacts")
        }


def record(scenario: str, **details: Any) -> None:
    EVIDENCE.append({"crash_point": scenario, **details})


@pytest.fixture(scope="module", autouse=True)
def _write_evidence() -> Any:
    yield
    directory = os.environ.get("VIDEO_EVIDENCE_DIR")
    if directory and EVIDENCE:
        Path(directory).mkdir(parents=True, exist_ok=True)
        (Path(directory) / "crash-recovery.json").write_text(json.dumps(EVIDENCE, indent=2))


def test_crash_after_accept_commit_replays_same_job(tmp_path: Path) -> None:
    crash("after-accept-commit", tmp_path)
    repo = repository(tmp_path)
    request = video_request()
    replay = repo.submit(request, request_fingerprint(request), admit_for(tmp_path))
    assert replay.created is False
    assert counts(tmp_path)["video_jobs"] == 1
    assert counts(tmp_path)["video_tasks"] == 5
    record("after-accept-commit", replay_created=replay.created, db=counts(tmp_path))


@pytest.mark.asyncio
async def test_crash_after_temp_write_and_after_rename(tmp_path: Path) -> None:
    for scenario in ("after-temp-write", "after-rename-before-commit"):
        directory = tmp_path / scenario
        directory.mkdir()
        clock = FakeClock()
        repo = repository(directory, clock)
        job_id = submit(repo, directory)
        crash(scenario, directory)
        rows = repo.task_rows(job_id)
        assert rows["source"]["status"] == "running"  # nothing committed after the reservation
        assert rows["source"]["result"] is None
        with sqlite3.connect(directory / "video.sqlite3") as connection:
            states = [row[0] for row in connection.execute("SELECT state FROM video_artifacts")]
        assert states == ["staged"]  # the rolled-back rename left the manifest discoverable
        clock.advance(60 + 2)
        media = FakeMedia()
        await drain(ProcessVideoTask(repo, media, FakeVideoAnalyzer(), {"io": 2, "cpu": 1}).run_once)
        assert media.calls["source"] == 1  # resumed once with a new lease
        assert repo.job_row(job_id)["status"] == "completed"  # type: ignore[index]
        leftovers = sorted(p.name for p in (directory / "artifacts").rglob("*") if p.is_file())
        clock.advance(24 * 3600)
        CleanupVideoJobs(repo).run_once()
        assert not any(p.is_file() for p in (directory / "artifacts").rglob("*"))
        record(scenario, files_before_gc=len(leftovers), stage_attempts=repo.task_rows(job_id)["source"]["attempts"])


def test_crash_inside_terminal_commit_leaves_no_partial_state(tmp_path: Path) -> None:
    repo = repository(tmp_path, FakeClock())
    job_id = submit(repo, tmp_path)
    crash("inside-terminal-commit", tmp_path)
    db = counts(tmp_path)
    assert (db["video_results"], db["video_callbacks"]) == (0, 0)
    assert repo.job_row(job_id)["status"] == "processing"  # type: ignore[index]
    assert repo.job_row(job_id)["finished_at"] is None  # type: ignore[index]
    record("inside-terminal-commit", db=db)


@pytest.mark.asyncio
async def test_crash_after_callback_reservation_keeps_budget(tmp_path: Path) -> None:
    clock = FakeClock(START)
    repo = repository(tmp_path, clock)
    job_id = submit(repo, tmp_path)
    await drain(ProcessVideoTask(repo, FakeMedia(), FakeVideoAnalyzer(), {"io": 2, "cpu": 1}).run_once)
    crash("after-callback-reservation", tmp_path)
    assert repo.callback_row(job_id)["reservations"] == 1  # type: ignore[index]
    clock.advance(60 + 5)
    receiver = Receiver()
    deliverer = DeliverVideoCallback(repo, HttpxCallbackTransport(security(), receiver.transport()), 1, 60)
    assert await deliverer.run_once()
    row = repo.callback_row(job_id)
    assert (row["status"], row["reservations"]) == ("delivered", 2)  # type: ignore[index]
    assert len(receiver.requests) == 1
    assert repo.task_rows(job_id)["analyze"]["attempts"] == 1
    record("after-callback-reservation", reservations=row["reservations"], http_requests=len(receiver.requests))  # type: ignore[index]
