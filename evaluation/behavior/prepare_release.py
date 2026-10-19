"""Record hashes and locked runtime versions for explicitly provisioned model assets."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from app.outbound.adapters.behavior.manifest import load_manifest, manifest_template


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    root = args.directory.resolve()
    face = hashlib.sha256((root / "face_landmarker.task").read_bytes()).hexdigest()
    pose = hashlib.sha256((root / "pose_landmarker_lite.task").read_bytes()).hexdigest()
    path = root / "manifest.json"
    # Do not silently replace an immutable release record.
    with path.open("x", encoding="utf-8") as output:
        output.write(manifest_template(face, pose))
    load_manifest(path, "mp-face-pose-v1")


if __name__ == "__main__":
    main()
