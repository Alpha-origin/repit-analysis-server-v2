from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.core.commands.cleanup_video_jobs import CleanupVideoJobs
from app.core.commands.process_video_task import ProcessVideoTask
from app.core.common.video.identity import request_fingerprint
from app.core.common.video.ports import ReplayConflictError, ReplayGoneError, SnapshotGone
from tests.video.helpers import FakeClock, FakeMedia, FakeVideoAnalyzer, admit_for, drain, repository, video_request

DAY = 86_400


def race(count: int, action: Callable[[int], Any]) -> list[Any]:
    barrier = threading.Barrier(count)
    results: list[Any] = [None] * count

    def run(index: int) -> None:
        barrier.wait()
        try:
            results[index] = action(index)
        except Exception as exc:
            results[index] = exc

    threads = [threading.Thread(target=run, args=(index,)) for index in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


def table_count(path: Path, table: str) -> int:
    with sqlite3.connect(path) as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608


def test_simultaneous_replays_create_one_job(tmp_path: Path) -> None:
    clock = FakeClock()
    repository(tmp_path, clock)  # create the schema first

    def accept(index: int) -> str:
        request = video_request(video={"fileUrl": video_request().video.file_url.replace("first", f"sig{index}")})
        repo = repository(tmp_path, clock)  # independent connection per "API worker"
        return repo.submit(request, request_fingerprint(request), admit_for(tmp_path)).job_id

    results = race(12, accept)
    assert len(set(results)) == 1
    assert table_count(tmp_path / "video.sqlite3", "video_jobs") == 1
    assert table_count(tmp_path / "video.sqlite3", "video_tasks") == 5
    assert table_count(tmp_path / "video.sqlite3", "video_dependencies") == 7

    def conflicting(index: int) -> str:
        request = video_request(requestId="contested", userId=str(index))
        return repository(tmp_path, clock).submit(request, request_fingerprint(request), admit_for(tmp_path)).job_id

    outcomes = race(8, conflicting)
    winners = [item for item in outcomes if isinstance(item, str)]
    assert len(winners) == 1
    assert all(isinstance(item, ReplayConflictError) for item in outcomes if not isinstance(item, str))


def test_parallel_claims_respect_lane_capacity(tmp_path: Path) -> None:
    clock = FakeClock()
    repo = repository(tmp_path, clock)
    for index in range(6):
        request = video_request(requestId=f"r{index}")
        repo.submit(request, request_fingerprint(request), admit_for(tmp_path))
    claims = race(10, lambda index: repository(tmp_path, clock).claim_stage({"io": 2}, 60))
    assert len([claim for claim in claims if claim is not None]) == 2
    assert len({claim.id for claim in claims if claim is not None}) == 2


@pytest.mark.asyncio
async def test_cleanup_races_do_not_reenqueue_or_lose_result(tmp_path: Path) -> None:
    clock = FakeClock()
    repo = repository(tmp_path, clock)
    request = video_request()
    job_id = repo.submit(request, request_fingerprint(request), admit_for(tmp_path)).job_id
    await drain(ProcessVideoTask(repo, FakeMedia(), FakeVideoAnalyzer(), {"io": 2, "cpu": 1}).run_once)
    clock.advance(90 * DAY)

    def act(index: int) -> Any:
        local = repository(tmp_path, clock)
        if index % 3 == 0:
            return CleanupVideoJobs(local).run_once()
        if index % 3 == 1:
            return local.snapshot(job_id)
        return local.submit(request, request_fingerprint(request), admit_for(tmp_path))

    results = race(9, act)
    for index, result in enumerate(results):
        if index % 3 == 1:
            assert isinstance(result, SnapshotGone)  # never an expired 200
        if index % 3 == 2:
            assert isinstance(result, ReplayGoneError)  # never re-enqueued
    path = tmp_path / "video.sqlite3"
    assert table_count(path, "video_jobs") == 0
    assert table_count(path, "video_tombstones") == 1
    assert table_count(path, "video_tasks") == 0
    assert sum(item.expired_jobs for item in results[0::3]) <= 1
