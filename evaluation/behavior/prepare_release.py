"""Verify pinned assets and record locked runtime versions without replacing a release."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from app.outbound.adapters.behavior.manifest import CHUNK_BYTES, ModelManifest, load_manifest, manifest_template


def prepare_release(directory: Path) -> Path:
    root = directory.resolve(strict=True)
    path = root / "manifest.json"
    hashes = []
    for name in ("face_landmarker.task", "pose_landmarker_lite.task"):
        asset = root / name
        if asset.is_symlink() or not asset.is_file():
            raise ValueError("model asset must be a regular release file")
        checksum = hashlib.sha256()
        with asset.open("rb") as source:
            while block := source.read(CHUNK_BYTES):
                checksum.update(block)
        hashes.append(checksum.hexdigest())
    # Validate the pinned hashes, package metadata and paths before creating any release record.
    body = manifest_template(*hashes)
    manifest = ModelManifest.model_validate_json(body)
    for model_asset in (manifest.face, manifest.pose):
        manifest.asset_path(path, model_asset)
    with path.open("x", encoding="utf-8") as output:
        output.write(body)
    load_manifest(path, "mp-face-pose-v1")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    prepare_release(args.directory)


if __name__ == "__main__":
    main()
