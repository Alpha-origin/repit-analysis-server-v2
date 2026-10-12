"""Real browser MediaRecorder WebM samples (see tests/video/browser_capture.py) through the real checks."""

from __future__ import annotations

import json
import os
from fractions import Fraction
from pathlib import Path

import pytest

from app.core.common.video.media_validation import HEADER_READ_BYTES, webm_declared_duration
from app.core.common.video.policy import VideoLimits
from app.outbound.adapters.video.media_backend import LocalVideoMediaBackend
from tests.video.helpers import security
from tests.video.media_fixtures import REQUIRED, require_media_tools


def samples() -> list[Path]:
    directory = os.environ.get("VIDEO_BROWSER_FIXTURES_DIR")
    if not directory:
        if REQUIRED:
            pytest.fail("VIDEO_BROWSER_FIXTURES_DIR is required when VIDEO_REAL_MEDIA_REQUIRED=1")
        pytest.skip("no browser samples; run tests/video/browser_capture.py and set VIDEO_BROWSER_FIXTURES_DIR")
    found = sorted(Path(directory).glob("browser-*.webm"))
    if not found:
        pytest.fail(f"no browser-*.webm samples in {directory}")
    return found


@pytest.mark.asyncio
async def test_browser_mediarecorder_webm_passes_real_checks() -> None:
    require_media_tools()
    backend = LocalVideoMediaBackend(security())
    results = {}
    for path in samples():
        # MediaRecorder writes no Duration element: the length must come from decoding, never an estimate.
        assert webm_declared_duration(path.read_bytes()[:HEADER_READ_BYTES]) is None
        inspected = await backend.inspect(str(path), VideoLimits())
        validated = await backend.validate(str(path), inspected, VideoLimits())
        assert inspected["container"] == "webm"
        assert inspected["codec"] in ("vp8", "vp9")
        assert inspected["declaredDuration"] is None
        assert 1_000 <= validated["durationMs"] <= 5_000
        assert validated["averageFps"] <= 60
        assert Fraction(inspected["timeBase"]) == Fraction(1, 1000)
        results[path.name] = {**{key: inspected[key] for key in ("codec", "timeBase")}, **validated}
    manifest = Path(os.environ["VIDEO_BROWSER_FIXTURES_DIR"]) / "capture-manifest.json"
    if manifest.exists():
        captured = json.loads(manifest.read_text())
        assert captured["browser"]
        evidence = os.environ.get("VIDEO_EVIDENCE_DIR")
        if evidence:
            Path(evidence).mkdir(parents=True, exist_ok=True)
            (Path(evidence) / "browser-media.json").write_text(
                json.dumps({"capture": captured, "checks": results}, indent=2)
            )
