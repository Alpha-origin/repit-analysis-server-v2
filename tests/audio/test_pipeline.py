from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.core.commands.process_audio_task import ProcessAudioTask
from app.core.common.audio.dto import AudioPolicy, IdempotencyConflictError
from app.outbound.adapters.audio.sqlite_repository import SqliteAudioRepository
from tests.audio.helpers import FakeBackend, FakeClient, FakeWebhook, request

CAPACITIES = {"cpu": 2, "inference": 1, "llm": 1, "callback": 1}


async def drain(processor: ProcessAudioTask) -> None:
    for _ in range(200):
        if not await processor.run_once():
            return
    pytest.fail("queue did not drain")


@pytest.mark.asyncio
async def test_twelve_answers_are_processed_once_and_collected(tmp_path: Path) -> None:
    repository = SqliteAudioRepository(tmp_path / "queue.db")
    manifest = request(12)
    job = repository.submit(manifest, AudioPolicy())
    backend, client = FakeBackend(), FakeClient()
    processor = ProcessAudioTask(repository, backend, client, FakeWebhook(), CAPACITIES)
    await drain(processor)
    snapshot = repository.snapshot(job)
    assert snapshot is not None
    assert snapshot["status"] == "completed"
    assert snapshot["completedTasks"] == snapshot["totalTasks"]
    assert len(snapshot["result"]["answers"]) == 12
    assert snapshot["result"]["answers"][0]["timing"]["status"] == "ready"
    assert snapshot["result"]["answers"][0]["fluency"]["acoustic_stuttering"]["status"] == "unavailable"
    assert backend.calls["transcribe"] == client.calls == 12
    assert repository.submit(manifest, AudioPolicy()) == job
    await drain(processor)
    assert backend.calls["transcribe"] == 12


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_stage", ["source", "normalize", "vad", "transcribe", "quality", "align"])
async def test_optional_failures_finish_without_erasing_other_results(tmp_path: Path, failed_stage: str) -> None:
    repository = SqliteAudioRepository(tmp_path / "queue.db")
    job = repository.submit(request(), AudioPolicy())
    processor = ProcessAudioTask(repository, FakeBackend(failed_stage), FakeClient(), FakeWebhook(), CAPACITIES)
    await drain(processor)
    snapshot = repository.snapshot(job)
    assert snapshot is not None
    assert snapshot["status"] == "completed"
    result = snapshot["result"]["answers"][0]
    if failed_stage == "transcribe":
        assert result["timing"]["silence"]["pause_count"] == 1
        assert result["timing"]["speed"] is None
        assert result["fluency"]["status"] == "unavailable"
    elif failed_stage == "vad":
        assert result["timing"]["status"] == "unavailable"
        assert result["fluency"]["final_sentence"] == "complete"
    elif failed_stage in {"source", "normalize"}:
        assert snapshot["result"]["status"] == "unusable"
    elif failed_stage == "align":
        assert result["timing"]["speed"] is not None
        assert result["timing"]["stability"] is None


@pytest.mark.asyncio
async def test_restart_uses_completed_stages(tmp_path: Path) -> None:
    path = tmp_path / "queue.db"
    repository = SqliteAudioRepository(path)
    job = repository.submit(request(), AudioPolicy())
    first_backend = FakeBackend()
    first = ProcessAudioTask(repository, first_backend, FakeClient(), FakeWebhook(), CAPACITIES)
    assert await first.run_once()  # source committed
    assert first_backend.calls["source"] == 1
    second_backend = FakeBackend()
    second = ProcessAudioTask(SqliteAudioRepository(path), second_backend, FakeClient(), FakeWebhook(), CAPACITIES)
    await drain(second)
    assert second_backend.calls["source"] == 0
    assert second_backend.calls["transcribe"] == 1
    assert repository.snapshot(job)["status"] == "completed"  # type: ignore[index]


def test_leases_fence_stale_results_and_enforce_shared_capacity(tmp_path: Path) -> None:
    path = tmp_path / "queue.db"
    first = SqliteAudioRepository(path)
    first.submit(request(2), AudioPolicy())
    task = first.claim({"cpu": 1}, 60)
    assert task is not None
    other = SqliteAudioRepository(path)
    assert other.claim({"cpu": 1}, 60) is None
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE audio_tasks SET lease_until=0 WHERE id=?", (task["id"],))
    reclaimed = other.claim({"cpu": 1}, 60)
    assert reclaimed is not None
    assert reclaimed["id"] == task["id"]
    assert reclaimed["token"] != task["token"]
    assert not first.finish(task, {"stale": True})
    assert not first.heartbeat(task["id"], task["token"], 60)
    assert other.finish(reclaimed, {"fresh": True})


def test_changed_manifest_or_policy_cannot_reuse_request_id(tmp_path: Path) -> None:
    repository = SqliteAudioRepository(tmp_path / "queue.db")
    repository.submit(request(), AudioPolicy())
    with pytest.raises(IdempotencyConflictError):
        repository.submit(request(2), AudioPolicy())
    with pytest.raises(IdempotencyConflictError):
        repository.submit(request(), AudioPolicy(pause_min_ms=300))


@pytest.mark.asyncio
async def test_callback_retry_does_not_repeat_analysis(tmp_path: Path) -> None:
    path = tmp_path / "queue.db"
    repository = SqliteAudioRepository(path)
    manifest = request().model_copy(update={"callback_url": "https://callbacks.example.test/result"})
    job = repository.submit(manifest, AudioPolicy())
    backend, client, webhook = FakeBackend(), FakeClient(), FakeWebhook(False)
    processor = ProcessAudioTask(repository, backend, client, webhook, CAPACITIES)
    await drain(processor)
    assert repository.snapshot(job)["callbackStatus"] == "pending"  # type: ignore[index]
    webhook.success = True
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE audio_tasks SET available=0 WHERE stage='callback'")
    await drain(processor)
    assert webhook.calls == 2
    assert backend.calls["transcribe"] == client.calls == 1
    assert repository.snapshot(job)["callbackStatus"] == "succeeded"  # type: ignore[index]
    assert "pauseCount" in webhook.payloads[-1]["answers"][0]["timing"]["silence"]
    assert "finalSentence" in webhook.payloads[-1]["answers"][0]["fluency"]
