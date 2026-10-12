"""Real FFmpeg/FFprobe verification on synthetic samples (set VIDEO_REAL_MEDIA_REQUIRED=1 to forbid skipping)."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.core.common.video.errors import VideoStageError
from app.core.common.video.policy import VideoLimits
from app.outbound.adapters.video.media_backend import LocalVideoMediaBackend
from tests.video.helpers import security
from tests.video.media_fixtures import corrupt_tail, ffmpeg, lavfi_video, remux, require_media_tools, vfr_webm

LIMITS = VideoLimits()
MANIFEST: list[dict[str, Any]] = []


async def check(path: Path, limits: VideoLimits = LIMITS) -> tuple[str, dict[str, Any]]:
    backend = LocalVideoMediaBackend(security())
    started = time.monotonic()
    try:
        inspected = await backend.inspect(str(path), limits)
        validated = await backend.validate(str(path), inspected, limits)
    except VideoStageError as exc:
        outcome: tuple[str, dict[str, Any]] = (exc.code, {})
    else:
        outcome = ("ok", {**inspected, **validated})
    MANIFEST.append(
        {
            "sample": path.name,
            "bytes": path.stat().st_size,
            "outcome": outcome[0],
            "seconds": round(time.monotonic() - started, 3),
            **{key: outcome[1].get(key) for key in ("container", "codec", "durationMs", "frameCount", "averageFps")},
        }
    )
    return outcome


@pytest.fixture(scope="module", autouse=True)
def _write_manifest() -> Any:
    yield
    directory = os.environ.get("VIDEO_EVIDENCE_DIR")
    if directory and MANIFEST:
        Path(directory).mkdir(parents=True, exist_ok=True)
        (Path(directory) / "media-manifest.json").write_text(json.dumps(MANIFEST, indent=2))


ACCEPTED: dict[str, Callable[[Path], Path]] = {
    "vp8-with-audio.webm": lambda p: lavfi_video(p, codec="libvpx", audio=True),
    "vp9.webm": lambda p: lavfi_video(p, codec="libvpx-vp9"),
    "h264-with-audio.mp4": lambda p: lavfi_video(p, codec="libx264", audio=True),
    "fps60.webm": lambda p: lavfi_video(p, codec="libvpx", rate="60", seconds=2),
    "fps59.94.mp4": lambda p: lavfi_video(p, codec="libx264", rate="60000/1001", seconds=2),
    "vfr.webm": lambda p: vfr_webm(p, [0, 16, 33, 50, 100, 300, 317, 334, 900]),
    "browser-like-jitter.webm": lambda p: vfr_webm(p, [0, 2, 19, 83, 176, 185, 251, 319, 385, 386, 452, 518, 585]),
    "portrait-1080x1920.webm": lambda p: lavfi_video(p, codec="libvpx", size="1080x1920", seconds=0.2),
}

REJECTED: dict[str, tuple[Callable[[Path], Path], str]] = {
    "fps61.webm": (lambda p: lavfi_video(p, codec="libvpx", rate="61", seconds=2), "VIDEO_LIMIT_EXCEEDED"),
    "fps120.mp4": (lambda p: lavfi_video(p, codec="libx264", rate="120", seconds=1), "VIDEO_LIMIT_EXCEEDED"),
    "vfr-burst.webm": (lambda p: vfr_webm(p, [0, 16, 33, 41, 58]), "VIDEO_LIMIT_EXCEEDED"),  # whole-file average
    "sustained-100fps-window.webm": (
        lambda p: vfr_webm(p, [index * 10 for index in range(70)] + [1500, 2500]),
        "VIDEO_LIMIT_EXCEEDED",
    ),
    "2000x1000.webm": (lambda p: lavfi_video(p, codec="libvpx", size="2000x1000", seconds=0.2), "VIDEO_LIMIT_EXCEEDED"),
    "vp9-in-mp4.mp4": (lambda p: lavfi_video(p, codec="libvpx-vp9"), "VIDEO_FORMAT_UNSUPPORTED"),
    "matroska.mkv": (
        lambda p: remux(lavfi_video(p.with_suffix(".src.webm"), codec="libvpx"), p),
        "VIDEO_FORMAT_UNSUPPORTED",
    ),
}


@pytest.mark.asyncio
async def test_supported_samples_pass_real_checks(tmp_path: Path) -> None:
    require_media_tools()
    for name, make in ACCEPTED.items():
        outcome, details = await check(make(tmp_path / name))
        assert outcome == "ok", name
        assert details["durationMs"] > 0


@pytest.mark.asyncio
async def test_wrong_codec_late_corruption_and_limit_overrun(tmp_path: Path) -> None:
    require_media_tools()
    for name, (make, expected) in REJECTED.items():
        outcome, _ = await check(make(tmp_path / name))
        assert outcome == expected, name
    clean = lavfi_video(tmp_path / "clean.webm", codec="libvpx-vp9", seconds=3)
    assert (await check(corrupt_tail(clean, tmp_path / "late-corruption.webm", keep_ratio=0.8)))[0] == (
        "VIDEO_DECODE_FAILED"
    )
    # Never truncate to pass: a file longer than the limit is rejected, not cut.
    long_clip = lavfi_video(tmp_path / "three-seconds.webm", codec="libvpx", seconds=3)
    assert (await check(long_clip, VideoLimits(max_duration_ms=2_000)))[0] == "VIDEO_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_exact_sixty_minutes_and_one_second_over(tmp_path: Path) -> None:
    require_media_tools()
    hour = lavfi_video(tmp_path / "hour-1fps.webm", codec="libvpx", size="64x48", rate="1", seconds=3600)
    outcome, details = await check(hour)
    assert (outcome, details["durationMs"]) == ("ok", 3_600_000)
    over = lavfi_video(tmp_path / "hour-plus-1s.webm", codec="libvpx", size="64x48", rate="1", seconds=3601)
    assert (await check(over))[0] == "VIDEO_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_full_hd_sixty_fps_resource_behaviour(tmp_path: Path) -> None:
    require_media_tools()
    clip = lavfi_video(tmp_path / "1080p60.mp4", codec="libx264", size="1920x1080", rate="60", seconds=2)
    started = time.monotonic()
    outcome, details = await check(clip)
    assert outcome == "ok"
    assert details["frameCount"] == 120
    # Recorded for operators; not a performance guarantee.
    MANIFEST.append({"sample": "1080p60-decode-seconds", "seconds": round(time.monotonic() - started, 3)})


@pytest.mark.asyncio
async def test_rotated_mp4_uses_display_geometry(tmp_path: Path) -> None:
    require_media_tools()
    source = lavfi_video(tmp_path / "wide.mp4", codec="libx264", size="1920x1080", seconds=0.2)
    rotated = tmp_path / "rotated.mp4"
    ffmpeg("-display_rotation", "90", "-i", str(source), "-c", "copy", str(rotated))
    outcome, details = await check(rotated)
    assert outcome == "ok"
    assert (details["displayLongEdge"], details["displayShortEdge"]) == (1920, 1080)
    assert abs(details["rotationDegrees"]) == 90
