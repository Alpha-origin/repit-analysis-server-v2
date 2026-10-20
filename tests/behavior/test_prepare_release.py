from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from evaluation.behavior.prepare_release import prepare_release

from app.outbound.adapters.behavior import manifest


@pytest.fixture
def assets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name, filename in (("face", "face_landmarker.task"), ("pose", "pose_landmarker_lite.task")):
        content = name.encode()
        (tmp_path / filename).write_bytes(content)
        monkeypatch.setitem(
            manifest.RELEASE_ASSETS, name, (hashlib.sha256(content).hexdigest(), "https://example.com/model")
        )
    monkeypatch.setattr(manifest, "version", lambda _: "test-runtime")
    return tmp_path


def test_preparation_records_a_verified_release_and_never_overwrites(assets: Path) -> None:
    path = prepare_release(assets)
    original = path.read_bytes()
    assert manifest.load_manifest(path, "mp-face-pose-v1").model_version == "mp-face-pose-v1"
    with pytest.raises(FileExistsError):
        prepare_release(assets)
    assert path.read_bytes() == original


@pytest.mark.parametrize("filename", ["face_landmarker.task", "pose_landmarker_lite.task"])
def test_modified_asset_never_creates_a_release(assets: Path, filename: str) -> None:
    (assets / filename).write_bytes(b"wrong model")
    with pytest.raises(ValueError, match="pinned release"):
        prepare_release(assets)
    assert not (assets / "manifest.json").exists()


def test_official_release_hashes_are_checked_before_package_metadata(tmp_path: Path) -> None:
    (tmp_path / "face_landmarker.task").write_bytes(b"not the official face model")
    (tmp_path / "pose_landmarker_lite.task").write_bytes(b"not the official pose model")
    with pytest.raises(ValueError, match="pinned release"):
        prepare_release(tmp_path)
    assert not (tmp_path / "manifest.json").exists()


def test_symlink_asset_never_creates_a_release(assets: Path) -> None:
    face = assets / "face_landmarker.task"
    face.rename(assets / "original.task")
    face.symlink_to(assets / "original.task")
    with pytest.raises(ValueError, match="regular release file"):
        prepare_release(assets)
    assert not (assets / "manifest.json").exists()
