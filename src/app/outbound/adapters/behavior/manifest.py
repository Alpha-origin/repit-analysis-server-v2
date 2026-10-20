from __future__ import annotations

import hashlib
import json
import sys
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from app.core.common.behavior.dto import BehaviorModel

MANIFEST_BYTES = 64 * 1_024
CHUNK_BYTES = 1_024 * 1_024


class ModelAsset(BehaviorModel):
    path: Annotated[str, Field(min_length=1, max_length=200)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    source_url: Annotated[str, Field(pattern=r"^https://", max_length=1_000)]


class RuntimeVersions(BehaviorModel):
    python: str
    mediapipe: str
    av: str
    numpy: str


class ModelManifest(BehaviorModel):
    manifest_version: Literal["1"]
    model_version: Literal["mp-face-pose-v1"]
    data_schema_version: Literal["behavior-v1"]
    thresholds_version: Literal["behavior-policy-v1"]
    runtime: RuntimeVersions
    face: ModelAsset
    pose: ModelAsset

    def asset_path(self, manifest: Path, asset: ModelAsset) -> Path:
        relative = Path(asset.path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("model asset must be inside the release directory")
        candidate = manifest.parent / relative
        if candidate.is_symlink() or not candidate.is_file():
            raise ValueError("model asset is not a regular file")
        if not candidate.resolve().is_relative_to(manifest.parent.resolve()):
            raise ValueError("model asset escapes release directory")
        return candidate


def load_manifest(path: Path, expected_version: str, expected_schema: str = "behavior-v1") -> ModelManifest:
    if not path.is_absolute() or path.is_symlink() or path.stat().st_size > MANIFEST_BYTES:
        raise ValueError("invalid model manifest file")
    manifest = ModelManifest.model_validate_json(path.read_bytes())
    if manifest.model_version != expected_version or manifest.data_schema_version != expected_schema:
        raise ValueError("model release versions do not match settings")
    if manifest.runtime.python != f"{sys.version_info.major}.{sys.version_info.minor}":
        raise ValueError("model Python runtime mismatch")
    for package in ("mediapipe", "av", "numpy"):
        if version(package) != getattr(manifest.runtime, package):
            raise ValueError("model package runtime mismatch")
    for asset in (manifest.face, manifest.pose):
        checksum = hashlib.sha256()
        with manifest.asset_path(path, asset).open("rb") as source:
            while block := source.read(CHUNK_BYTES):
                checksum.update(block)
        if checksum.hexdigest() != asset.sha256:
            raise ValueError("model asset checksum mismatch")
    return manifest


def manifest_template(face_hash: str, pose_hash: str) -> str:
    """Build a release record after explicit operator-controlled asset provisioning."""
    base = "https://storage.googleapis.com/mediapipe-models"
    return (
        json.dumps(
            {
                "manifestVersion": "1",
                "modelVersion": "mp-face-pose-v1",
                "dataSchemaVersion": "behavior-v1",
                "thresholdsVersion": "behavior-policy-v1",
                "runtime": {
                    "python": f"{sys.version_info.major}.{sys.version_info.minor}",
                    **{package: version(package) for package in ("mediapipe", "av", "numpy")},
                },
                "face": {
                    "path": "face_landmarker.task",
                    "sha256": face_hash,
                    "sourceUrl": f"{base}/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
                },
                "pose": {
                    "path": "pose_landmarker_lite.task",
                    "sha256": pose_hash,
                    "sourceUrl": f"{base}/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task",
                },
            },
            indent=2,
        )
        + "\n"
    )
