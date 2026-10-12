from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.commands.deliver_video_callback import DeliverVideoCallback
from app.core.commands.process_video_task import ProcessVideoTask
from app.core.common.video.ports import SnapshotFound
from app.outbound.adapters.httpx_callback_transport import HttpxCallbackTransport
from app.outbound.adapters.video.sqlite_repository import SqliteVideoRepository
from tests.video.helpers import (
    FakeClock,
    FakeMedia,
    FakeVideoAnalyzer,
    Receiver,
    SwappableSecurity,
    drain,
    repository,
    security,
    submit,
)

DELAYS = (5, 15, 60, 300, 900)


async def finished_job(tmp_path: Path, clock: FakeClock) -> tuple[SqliteVideoRepository, str]:
    repo = repository(tmp_path, clock)
    job_id = submit(repo, tmp_path)
    await drain(ProcessVideoTask(repo, FakeMedia(), FakeVideoAnalyzer(), {"io": 2, "cpu": 1}).run_once)
    return repo, job_id


def deliverer(repo: SqliteVideoRepository, receiver: Receiver, provider: object | None = None) -> DeliverVideoCallback:
    transport = HttpxCallbackTransport(provider or security(), receiver.transport())  # type: ignore[arg-type]
    return DeliverVideoCallback(repo, transport, capacity=1, lease_seconds=60)


@pytest.mark.asyncio
async def test_persisted_budget_and_exact_schedule(tmp_path: Path) -> None:
    clock = FakeClock()
    repo, job_id = await finished_job(tmp_path, clock)
    receiver = Receiver(statuses=[500] * 10)
    worker = deliverer(repo, receiver)
    for attempt, delay in enumerate((*DELAYS, None), start=1):
        if attempt > 1:
            # A restarted worker (fresh object) sees the same persisted budget and schedule.
            worker = deliverer(repository(tmp_path, clock), receiver)
        assert await worker.run_once()
        row = repo.callback_row(job_id)
        assert row is not None
        assert row["reservations"] == attempt
        if delay is None:
            assert row["status"] == "failed"
            break
        assert row["status"] == "pending"
        assert row["next_at"] == clock.now() + delay  # waits live in the DB; no worker sleeps
        clock.advance(delay - 1)
        assert await worker.run_once() is False
        clock.advance(1)
    assert len(receiver.requests) == 6
    clock.advance(10_000)
    assert await worker.run_once() is False  # budget exhausted: never a 7th attempt
    assert len(receiver.requests) == 6


@pytest.mark.asyncio
async def test_crash_before_and_after_http_preserves_budget(tmp_path: Path) -> None:
    clock = FakeClock()
    repo, job_id = await finished_job(tmp_path, clock)
    receiver = Receiver()
    # Crash right after the reservation commit (no HTTP happened).
    claim = repo.claim_callback(1, 60)
    assert claim is not None
    clock.advance(60)  # lease expires
    assert repo.claim_callback(1, 60) is None  # waits lease_until + 5s
    clock.advance(5)
    second = repo.claim_callback(1, 60)
    assert second is not None
    assert second.reservation == 2  # the crashed reservation still counts
    assert not repo.finish_callback(claim, "delivered", "http_200")  # the dead worker cannot finish
    # Crash after a successful HTTP whose result was never stored: the receiver sees a duplicate.
    transport = HttpxCallbackTransport(security(), receiver.transport())
    await transport.post_once(second.url, second.body, timeout_seconds=5)
    clock.advance(60 + 15)
    assert await deliverer(repo, receiver).run_once()
    row = repo.callback_row(job_id)
    assert (row["status"], row["reservations"]) == ("delivered", 3)  # type: ignore[index]
    assert len(receiver.requests) == 2
    assert len(receiver.stored) == 1  # idempotent receiver keeps one result
    assert repo.task_rows(job_id)["analyze"]["attempts"] == 1  # never re-analyzed


@pytest.mark.asyncio
async def test_permanent_delivery_failure_preserves_result(tmp_path: Path) -> None:
    clock = FakeClock()
    repo, job_id = await finished_job(tmp_path, clock)
    before = repo.snapshot(job_id)
    receiver = Receiver(statuses=[400])
    assert await deliverer(repo, receiver).run_once()
    assert len(receiver.requests) == 1
    after = repo.snapshot(job_id)
    assert isinstance(before, SnapshotFound)
    assert isinstance(after, SnapshotFound)
    assert after.snapshot.callback_status == "failed"
    assert after.snapshot.status == "completed"
    assert after.snapshot.result == before.snapshot.result  # recoverable through GET


@pytest.mark.asyncio
async def test_current_token_and_block_apply_to_old_jobs(tmp_path: Path) -> None:
    clock = FakeClock()
    repo, job_id = await finished_job(tmp_path, clock)
    provider = SwappableSecurity(security(callback_token="old-token"))
    receiver = Receiver(token="new-token")
    worker = deliverer(repo, receiver, provider)
    assert await worker.run_once()
    assert receiver.requests[-1].headers["x-internal-token"] == "old-token"  # rejected 401 -> failed
    assert repo.callback_row(job_id)["status"] == "failed"  # type: ignore[index]

    other = tmp_path / "second"
    other.mkdir()
    repo, job_id = await finished_job(other, clock)
    receiver = Receiver(token="new-token", statuses=[503])
    provider = SwappableSecurity(security(callback_token="old-token"))
    worker = deliverer(repo, receiver, provider)
    receiver.token = "old-token"
    assert await worker.run_once()  # 503 -> retry
    provider.provider = security(callback_token="new-token")  # restart with the rotated token
    receiver.token = "new-token"
    clock.advance(5)
    assert await worker.run_once()
    assert receiver.requests[-1].headers["x-internal-token"] == "new-token"
    assert repo.callback_row(job_id)["status"] == "delivered"  # type: ignore[index]
    body = json.loads(receiver.requests[-1].content)
    assert body["jobId"] == job_id
