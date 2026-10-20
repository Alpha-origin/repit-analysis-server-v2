from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from app.core.common.video.dto import AnalysisOutcome, PreparedVideo
from app.outbound.adapters.behavior import cli


def prepared(tmp_path: Path) -> PreparedVideo:
    path = tmp_path / "source.mp4"
    path.write_bytes(b"fixture")
    return PreparedVideo(
        video_id="501",
        path=str(path),
        sha256=hashlib.sha256(b"fixture").hexdigest(),
        bytes=7,
        container="mp4",
        codec="h264",
        display_long_edge=640,
        display_short_edge=480,
        rotation_degrees=0,
        frame_count=300,
        duration_ms=60_000,
        average_fps=5,
    )


def test_source_binding_rejects_changed_bytes(tmp_path: Path) -> None:
    source = prepared(tmp_path)
    cli._verify_source(source)
    Path(source.path).write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        cli._verify_source(source)


def test_native_output_cannot_pollute_success_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    source = prepared(tmp_path)
    fixture = json.loads((Path(__file__).parent / "fixtures/behavior-ready.json").read_text())

    def model(*_: object) -> AnalysisOutcome:
        os.write(1, b"native diagnostics\n")
        return AnalysisOutcome(status="ready", data=fixture, error=None)

    monkeypatch.setattr(cli, "analyze", model)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "analyzer",
            "--model-path",
            str(tmp_path / "manifest.json"),
            "--model-version",
            "mp-face-pose-v1",
            "--input-json",
            source.model_dump_json(),
        ],
    )
    cli.main()
    output = capfd.readouterr()
    assert json.loads(output.out)["data"]["schemaVersion"] == "behavior-v1"
    assert "native diagnostics" in output.err


def test_failure_output_does_not_leak_source_or_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    source = prepared(tmp_path)

    def model(*_: object) -> AnalysisOutcome:
        raise ValueError("private model path and user data")

    monkeypatch.setattr(cli, "analyze", model)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "analyzer",
            "--model-path",
            str(tmp_path / "manifest.json"),
            "--model-version",
            "mp-face-pose-v1",
            "--input-json",
            source.model_dump_json(),
        ],
    )
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == 1
    output = capfd.readouterr()
    assert output.out == ""
    assert output.err == "behavior analyzer failed: INTERNAL_ERROR\n"
