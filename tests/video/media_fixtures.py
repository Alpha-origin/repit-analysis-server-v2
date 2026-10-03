"""Synthetic media generated with FFmpeg at test time. Never personal recordings; nothing is committed."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

TOOLS_AVAILABLE = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
REQUIRED = os.environ.get("VIDEO_REAL_MEDIA_REQUIRED") == "1"


def require_media_tools() -> None:
    """Missing tools FAIL when real-media QA is required, instead of silently skipping."""
    if TOOLS_AVAILABLE:
        return
    if REQUIRED:
        pytest.fail("VIDEO_REAL_MEDIA_REQUIRED=1 but ffmpeg/ffprobe are not installed")
    pytest.skip("ffmpeg/ffprobe not installed (set VIDEO_REAL_MEDIA_REQUIRED=1 to make this a failure)")


def ffmpeg(*arguments: str) -> None:
    binary = shutil.which("ffmpeg")
    assert binary is not None
    subprocess.run([binary, "-nostdin", "-v", "error", "-y", *arguments], check=True, timeout=300)  # noqa: S603


def lavfi_video(
    path: Path,
    *,
    codec: str = "libvpx-vp9",
    size: str = "320x240",
    rate: str = "30",
    seconds: float = 1.0,
    extra: tuple[str, ...] = (),
    audio: bool = False,
    output_options: tuple[str, ...] = (),
) -> Path:
    inputs = ["-f", "lavfi", "-i", f"testsrc2=size={size}:rate={rate}"]
    if audio:
        inputs += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"]
    codec_options = ["-c:v", codec, "-pix_fmt", "yuv420p"]
    if codec.startswith("libvpx"):
        codec_options += ["-b:v", "200k", "-deadline", "realtime", "-cpu-used", "8"]
    if codec == "libx264":
        codec_options += ["-preset", "ultrafast"]
    audio_options = ["-c:a", "libopus" if path.suffix == ".webm" else "aac", "-shortest"] if audio else []
    ffmpeg(*inputs, "-t", str(seconds), *codec_options, *audio_options, *extra, *output_options, str(path))
    return path


def piped_webm(path: Path, frames: int, rate: int = 10) -> Path:
    """WebM written to a pipe with a frame count (not -t): the muxer cannot store a Duration element."""
    binary = shutil.which("ffmpeg")
    assert binary is not None
    with path.open("wb") as output:
        subprocess.run(  # noqa: S603
            [
                binary,
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=size=160x120:rate={rate}",
                "-frames:v",
                str(frames),
                "-c:v",
                "libvpx",
                "-f",
                "webm",
                "pipe:1",
            ],
            stdout=output,
            check=True,
            timeout=120,
        )
    return path


def remux(source: Path, target: Path, *options: str) -> Path:
    ffmpeg("-i", str(source), "-c", "copy", *options, str(target))
    return target


def vfr_webm(path: Path, timestamps_ms: list[int]) -> Path:
    """Build a WebM whose frame PTS are exactly ``timestamps_ms`` (1 ms container time base).

    A 1000 fps source has one frame per millisecond; ``select`` keeps the wanted ones and passthrough
    timing preserves their original timestamps (true variable frame rate).
    """
    expression = "+".join(f"eq(n\\,{stamp})" for stamp in timestamps_ms)
    duration = (max(timestamps_ms) + 1) / 1000  # bounded source: select must not wait for frames forever
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size=160x120:rate=1000:duration={duration},select='{expression}'",
        "-fps_mode",
        "passthrough",
        "-c:v",
        "libvpx",
        "-b:v",
        "100k",
        "-deadline",
        "realtime",
        "-pix_fmt",
        "yuv420p",
        str(path),
    )
    return path


def corrupt_tail(source: Path, target: Path, *, keep_ratio: float = 0.6, garbage: int = 4096) -> Path:
    data = source.read_bytes()
    cut = int(len(data) * keep_ratio)
    target.write_bytes(data[:cut] + bytes((index * 73) % 256 for index in range(garbage)) + data[cut + garbage :])
    return target
