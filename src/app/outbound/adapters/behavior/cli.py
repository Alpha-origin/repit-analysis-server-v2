"""Offline analyzer. All native output is isolated from the one JSON stdout document."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from app.core.common.behavior.aggregate import BehaviorAggregator
from app.core.common.video.dto import AnalysisOutcome, PreparedVideo
from app.outbound.adapters.behavior.frames import samples
from app.outbound.adapters.behavior.manifest import load_manifest
from app.outbound.adapters.behavior.mediapipe_models import MediaPipeModels

if TYPE_CHECKING:
    from collections.abc import Iterator

OUTPUT_BYTES = 1_024 * 1_024
READ_BYTES = 1_024 * 1_024


@contextmanager
def native_output_to_stderr() -> Iterator[None]:
    sys.stdout.flush()
    descriptor = os.dup(1)
    try:
        os.dup2(2, 1)
        yield
    finally:
        sys.stdout.flush()
        os.dup2(descriptor, 1)
        os.close(descriptor)


def _verify_source(prepared: PreparedVideo) -> None:
    path = Path(prepared.path)
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or path.stat().st_size != prepared.bytes:
        raise ValueError("validated source changed")
    checksum = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(READ_BYTES):
            checksum.update(block)
    if checksum.hexdigest() != prepared.sha256:
        raise ValueError("validated source checksum changed")


def analyze(prepared: PreparedVideo, model_path: Path, model_version: str) -> AnalysisOutcome:

    manifest = load_manifest(model_path, model_version)
    _verify_source(prepared)
    aggregator = BehaviorAggregator(prepared.video_id, prepared.duration_ms, model_version)
    models = MediaPipeModels(manifest, model_path)
    try:
        for sample in samples(prepared):
            aggregator.add(models.observe(sample))
    finally:
        models.close()
    return aggregator.finish()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--input-json", required=True)
    args = parser.parse_args()
    try:
        prepared = PreparedVideo.model_validate_json(args.input_json)
        with TemporaryDirectory(prefix="repit-behavior-cache-") as cache, native_output_to_stderr():
            # MediaPipe's optional plotting import must not write caches into a user's home.
            os.environ["MPLCONFIGDIR"] = cache
            outcome = analyze(prepared, args.model_path, args.model_version)
        body = json.dumps(outcome.wire(), ensure_ascii=False, allow_nan=False)
        if len(body.encode("utf-8")) > OUTPUT_BYTES:
            raise ValueError("analyzer output exceeds limit")
    except Exception:
        # Never print exception details: argv contains source paths and source metadata.
        sys.stderr.write("behavior analyzer failed: INTERNAL_ERROR\n")
        raise SystemExit(1) from None
    sys.stdout.write(body + "\n")


if __name__ == "__main__":
    main()
