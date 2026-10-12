from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.main.video_worker import WorkerBootError, build_worker
from tests.video.helpers import FakeMedia, callback_settings, repository, submit, video_settings

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_once_and_lane_startup(tmp_path: Path) -> None:
    settings = video_settings(tmp_path)
    io_only = build_worker(("io",), callback_settings=callback_settings(), video_settings=settings, media=FakeMedia())
    assert io_only.deliverer is None
    assert io_only.processor is not None
    assert io_only.processor.capacities == {"io": 2}
    assert await io_only.run_once() is False  # no task: returns immediately
    submit(repository(tmp_path), tmp_path)
    assert await io_only.run_once() is True  # source only
    assert await io_only.run_once() is False  # inspect belongs to the cpu lane
    with pytest.raises(WorkerBootError):
        build_worker(("gpu",), callback_settings=callback_settings(), video_settings=settings)


def test_production_missing_token_fails_before_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENVIRONMENT", "production")
    monkeypatch.delenv("APP_INTERNAL_CALLBACK_TOKEN", raising=False)
    with pytest.raises(ValidationError):
        build_worker(("callback",), video_settings=video_settings(tmp_path))
    assert not (tmp_path / "video.sqlite3").exists()  # failed before the database was created


def test_missing_probe_is_environment_failure(tmp_path: Path) -> None:
    with pytest.raises(WorkerBootError, match="ffprobe"):
        build_worker(
            ("cpu",),
            callback_settings=callback_settings(),
            video_settings=video_settings(tmp_path),
            ffprobe=str(tmp_path / "no-ffprobe"),
        )
    assert not (tmp_path / "video.sqlite3").exists()


def test_callback_only_worker_needs_no_media_tools(tmp_path: Path) -> None:
    worker = build_worker(
        ("callback",),
        callback_settings=callback_settings(),
        video_settings=video_settings(tmp_path, enabled=False),  # API admission off; draining still works
        ffprobe=str(tmp_path / "no-ffprobe"),
    )
    assert worker.processor is None
    assert worker.deliverer is not None


def test_worker_modules_import_no_model_dependencies() -> None:
    code = (
        "import sys; import app.main.video_worker, app.main.video_cleanup;"
        "bad=[m for m in sys.modules if m.split('.')[0] in {'anthropic','faster_whisper','silero_vad','torch'}"
        " or m.startswith('app.main.audio') or m.startswith('app.core.common.audio')];"
        "print(','.join(bad)); sys.exit(1 if bad else 0)"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout
