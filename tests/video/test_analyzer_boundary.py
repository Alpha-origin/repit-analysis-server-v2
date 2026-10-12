from __future__ import annotations

import ast
import json
import sqlite3
from pathlib import Path

import pytest

from app.core.commands.process_video_task import ProcessVideoTask
from app.core.common.video.errors import VideoStageError
from app.main.video_worker import build_worker
from app.outbound.adapters.video.analyzer import UnconfiguredVideoAnalyzer
from tests.video.helpers import (
    FakeMedia,
    FakeVideoAnalyzer,
    callback_settings,
    drain,
    partial_outcome,
    repository,
    submit,
    video_settings,
)

SRC = Path(__file__).resolve().parents[2] / "src"


def _body(tmp_path: Path) -> dict[str, object]:
    with sqlite3.connect(tmp_path / "video.sqlite3") as connection:
        return json.loads(connection.execute("SELECT body FROM video_results").fetchone()[0])  # type: ignore[no-any-return]


@pytest.mark.asyncio
async def test_production_unconfigured_is_completed_unavailable(tmp_path: Path) -> None:
    worker = build_worker(
        ("io", "cpu"),
        callback_settings=callback_settings(),
        video_settings=video_settings(tmp_path),
        media=FakeMedia(),  # only the media is faked; the analyzer is the real service default
    )
    assert worker.processor is not None
    assert isinstance(worker.processor.analyzer, UnconfiguredVideoAnalyzer)
    repo = repository(tmp_path)
    job_id = submit(repo, tmp_path)
    await drain(worker.run_once)
    body = _body(tmp_path)
    assert body["status"] == "unavailable"
    assert body["error"] is None
    result = body["result"]
    assert isinstance(result, dict)
    assert result["error"] is None  # the file itself was fine
    assert result["durationMs"] == 4_000
    assert result["analysis"] == {
        "status": "unavailable",
        "data": None,
        "error": {
            "code": "ANALYZER_NOT_CONFIGURED",
            "message": result["analysis"]["error"]["message"],
            "retryable": False,
        },
    }
    assert repo.job_row(job_id)["status"] == "completed"  # type: ignore[index]


@pytest.mark.asyncio
async def test_fake_is_test_only_and_file_failure_is_not_analyzer_success(tmp_path: Path) -> None:
    # 1) No module under src/ imports tests, and no fake/flag selects another analyzer.
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("tests"), path
            if isinstance(node, ast.Import):
                assert not any(alias.name.startswith("tests") for alias in node.names), path
        assert "FakeVideoAnalyzer" not in path.read_text(), path
    # 2) Only test DI produces ready/partial.
    repo = repository(tmp_path)
    submit(repo, tmp_path)
    analyzer = FakeVideoAnalyzer(partial_outcome())
    await drain(ProcessVideoTask(repo, FakeMedia(), analyzer, {"io": 2, "cpu": 1}).run_once)
    assert _body(tmp_path)["status"] == "partial"
    # 3) An invalid file never reaches the analyzer and is never reported as analyzer-not-configured.
    other = tmp_path / "invalid"
    other.mkdir()
    repo = repository(other)
    submit(repo, other)
    analyzer = FakeVideoAnalyzer()
    media = FakeMedia(fail={"inspect": VideoStageError("VIDEO_FORMAT_UNSUPPORTED")})
    await drain(ProcessVideoTask(repo, media, analyzer, {"io": 2, "cpu": 1}).run_once)
    body = _body(other)
    assert analyzer.calls == []
    assert body["result"]["error"]["code"] == "VIDEO_FORMAT_UNSUPPORTED"  # type: ignore[index]
    assert body["result"]["analysis"]["error"]["code"] == "VIDEO_FORMAT_UNSUPPORTED"  # type: ignore[index]


@pytest.mark.asyncio
async def test_analyzer_error_after_valid_file_keeps_result_error_null(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    submit(repo, tmp_path)
    analyzer = FakeVideoAnalyzer(error=VideoStageError("EXTERNAL_SERVICE_UNAVAILABLE"))
    await drain(ProcessVideoTask(repo, FakeMedia(), analyzer, {"io": 2, "cpu": 1}).run_once)
    body = _body(tmp_path)
    result = body["result"]
    assert isinstance(result, dict)
    assert result["error"] is None
    assert result["analysis"]["error"]["code"] == "EXTERNAL_SERVICE_UNAVAILABLE"
    assert result["durationMs"] == 4_000
