from __future__ import annotations

import hashlib
import json
from importlib.util import find_spec
from pathlib import Path

import pytest

from app.main.video_config import AnalyzerSettings, VideoSettings
from app.main.video_worker import WorkerBootError, configured_analyzer
from app.outbound.adapters.behavior import manifest as release_manifest
from app.outbound.adapters.behavior.manifest import load_manifest, manifest_template

if any(find_spec(package) is None for package in ("numpy", "mediapipe", "av")):
    pytest.skip("behavior extra is not installed", allow_module_level=True)


@pytest.fixture(autouse=True)
def synthetic_assets(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, content in (("face", b"face"), ("pose", b"pose")):
        monkeypatch.setitem(
            release_manifest.RELEASE_ASSETS,
            name,
            (hashlib.sha256(content).hexdigest(), release_manifest.RELEASE_ASSETS[name][1]),
        )


def release(tmp_path: Path) -> Path:
    face, pose = tmp_path / "face_landmarker.task", tmp_path / "pose_landmarker_lite.task"
    face.write_bytes(b"face")
    pose.write_bytes(b"pose")
    path = tmp_path / "manifest.json"
    path.write_text(manifest_template(hashlib.sha256(b"face").hexdigest(), hashlib.sha256(b"pose").hexdigest()))
    return path


def test_hash_runtime_and_release_binding(tmp_path: Path) -> None:
    path = release(tmp_path)
    assert load_manifest(path, "mp-face-pose-v1").data_schema_version == "behavior-v1"
    (tmp_path / "face_landmarker.task").write_bytes(b"different")
    with pytest.raises(ValueError, match="checksum"):
        load_manifest(path, "mp-face-pose-v1")


@pytest.mark.parametrize("mutation", ["runtime", "version", "traversal", "symlink", "replacement", "source"])
def test_reject_invalid_release(tmp_path: Path, mutation: str) -> None:
    path = release(tmp_path)
    raw = json.loads(path.read_text())
    if mutation == "runtime":
        raw["runtime"]["mediapipe"] = "not-installed"
    elif mutation == "version":
        raw["dataSchemaVersion"] = "unknown"
    elif mutation == "traversal":
        raw["face"]["path"] = "../outside.task"
    elif mutation == "replacement":
        (tmp_path / "face_landmarker.task").write_bytes(b"substituted model")
        raw["face"]["sha256"] = hashlib.sha256(b"substituted model").hexdigest()
    elif mutation == "source":
        raw["face"]["sourceUrl"] = "https://example.com/different.task"
    else:
        (tmp_path / "link.task").symlink_to(tmp_path / "face_landmarker.task")
        raw["face"]["path"] = "link.task"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match=r"model|ModelManifest"):
        load_manifest(path, "mp-face-pose-v1")


def test_cpu_configuration_fails_before_claiming_invalid_release(tmp_path: Path) -> None:
    executable = tmp_path / "analyzer"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    path = release(tmp_path)
    (tmp_path / "face_landmarker.task").write_bytes(b"changed")
    settings = VideoSettings(
        analyzer=AnalyzerSettings(
            backend="process",
            executable=executable,
            model_path=path,
            model_version="mp-face-pose-v1",
            data_schema_version="behavior-v1",
        )
    )
    with pytest.raises(WorkerBootError, match="release"):
        configured_analyzer(settings)
