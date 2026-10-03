from __future__ import annotations

import logging
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.commands.process_video_task import ProcessVideoTask
from app.main.video_cleanup import build_cleanup
from tests.video.helpers import (
    FakeClock,
    FakeMedia,
    FakeVideoAnalyzer,
    callback_settings,
    drain,
    repository,
    submit,
    video_request,
    video_settings,
)

DAY = 86_400


async def _finished_jobs(tmp_path: Path, clock: FakeClock, count: int) -> None:
    repo = repository(tmp_path, clock)
    for index in range(count):
        submit(repo, tmp_path, video_request(requestId=f"req-{index}"))
    await drain(ProcessVideoTask(repo, FakeMedia(), FakeVideoAnalyzer(), {"io": 2, "cpu": 1}).run_once, limit=500)


@pytest.mark.asyncio
async def test_once_and_repeat_cleanup(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    clock = FakeClock()
    await _finished_jobs(tmp_path, clock, 5)
    cleanup = build_cleanup(
        callback_settings=callback_settings(), video_settings=video_settings(tmp_path), clock=clock, batch_size=2
    )
    assert cleanup.run_once().expired_jobs == 0  # nothing eligible yet
    clock.advance(90 * DAY)
    caplog.set_level(logging.INFO)
    first = cleanup.run_once()
    assert (first.collected_artifacts, first.expired_jobs) == (2, 2)  # batch boundary respected
    second = cleanup.run_once()
    third = cleanup.run_once()
    assert first.expired_jobs + second.expired_jobs + third.expired_jobs == 5
    assert cleanup.run_once().expired_jobs == 0
    # Only counts and safe event names are logged.
    assert "video.cleanup.pass" in caplog.text
    assert "artifacts/" not in caplog.text
    assert "X-Amz" not in caplog.text


def test_cleanup_boot_failure_and_concurrent_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENVIRONMENT", "production")
    monkeypatch.delenv("APP_INTERNAL_CALLBACK_TOKEN", raising=False)
    with pytest.raises(ValidationError):
        build_cleanup(video_settings=video_settings(tmp_path))
    assert not (tmp_path / "video.sqlite3").exists()
    with pytest.raises(ValueError, match="batch"):
        build_cleanup(callback_settings=callback_settings(), video_settings=video_settings(tmp_path), batch_size=0)

    clock = FakeClock()
    import asyncio  # noqa: PLC0415

    asyncio.run(_finished_jobs(tmp_path, clock, 6))
    clock.advance(90 * DAY)
    cleaners = [
        build_cleanup(callback_settings=callback_settings(), video_settings=video_settings(tmp_path), clock=clock)
        for _ in range(3)
    ]
    results = []

    def run(index: int) -> None:
        results.append(cleaners[index].run_once())

    threads = [threading.Thread(target=run, args=(index,)) for index in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(item.expired_jobs for item in results) == 6  # each job transitioned exactly once
    assert sum(item.collected_artifacts for item in results) == 6  # and each file collected once
