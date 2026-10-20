from __future__ import annotations

import hashlib
import os
import shutil
from importlib import import_module
from pathlib import Path
from typing import Literal

import pytest

from app.core.common.behavior.dto import BehaviorData
from app.core.common.video.dto import PreparedVideo
from app.core.common.video.policy import VideoLimits
from app.outbound.adapters.video.process_analyzer import ProcessVideoAnalyzer

CODEC_NAMES: dict[str, Literal["h264", "vp8", "vp9"]] = {"libx264": "h264", "libvpx": "vp8", "libvpx-vp9": "vp9"}


def real_inputs() -> tuple[Path, Path, Path]:
    manifest = os.environ.get("BEHAVIOR_MODEL_MANIFEST")
    image = os.environ.get("BEHAVIOR_TEST_PORTRAIT")
    executable = shutil.which("repit-behavior-analyzer")
    if not manifest or not image or not executable:
        if os.environ.get("BEHAVIOR_REAL_MODELS_REQUIRED") == "1":
            pytest.fail("real behavior models, portrait and executable are required")
        pytest.skip("explicit model manifest and test portrait are not configured")
    return Path(manifest), Path(image), Path(executable)


@pytest.mark.parametrize(("codec", "extension"), [("libx264", "mp4"), ("libvpx", "webm"), ("libvpx-vp9", "webm")])
@pytest.mark.asyncio
async def test_actual_cli_models_and_codec_output(
    tmp_path: Path, codec: str, extension: Literal["webm", "mp4"]
) -> None:
    manifest, image, executable = real_inputs()
    av = import_module("av")
    np = import_module("numpy")
    from PIL import Image  # noqa: PLC0415 -- optional inference dependency

    path = tmp_path / f"portrait.{extension}"
    rgb = np.array(Image.open(image).convert("RGB").resize((480, 640)))
    with av.open(str(path), "w") as output:
        stream = output.add_stream(codec, rate=5)
        stream.width, stream.height, stream.pix_fmt = 480, 640, "yuv420p"
        for index in range(60):
            frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
            frame.pts = index
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    contents = path.read_bytes()
    prepared = PreparedVideo(
        video_id="real-portrait",
        path=str(path),
        bytes=len(contents),
        sha256=hashlib.sha256(contents).hexdigest(),
        container=extension,
        codec=CODEC_NAMES[codec],
        display_long_edge=640,
        display_short_edge=480,
        rotation_degrees=0,
        frame_count=60,
        duration_ms=12_000,
        average_fps=5,
    )
    outcome = await ProcessVideoAnalyzer(executable, manifest, "mp-face-pose-v1", "behavior-v1").analyze(
        prepared, VideoLimits()
    )
    assert outcome.status in ("ready", "partial")
    data = BehaviorData.model_validate(outcome.data)
    assert data.video_id == "real-portrait"
    assert data.sampled_frame_count == 60
    assert data.metrics["faceVisibleRatio"].value is not None
    assert data.metrics["faceVisibleRatio"].value > 0.8
    assert "COMPONENT_FAILURE" not in data.warnings


@pytest.mark.parametrize(("frames", "expected"), [(5, "unavailable"), (60, "partial")])
@pytest.mark.asyncio
async def test_actual_models_keep_short_video_and_zero_face_distinct(
    tmp_path: Path, frames: int, expected: str
) -> None:
    manifest, _, executable = real_inputs()
    av = import_module("av")
    np = import_module("numpy")
    path = tmp_path / "black.mp4"
    rgb = np.zeros((240, 320, 3), dtype=np.uint8)
    with av.open(str(path), "w") as output:
        stream = output.add_stream("libx264", rate=5)
        stream.width, stream.height, stream.pix_fmt = 320, 240, "yuv420p"
        for index in range(frames):
            frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
            frame.pts = index
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    contents = path.read_bytes()
    prepared = PreparedVideo(
        video_id="real-black",
        path=str(path),
        bytes=len(contents),
        sha256=hashlib.sha256(contents).hexdigest(),
        container="mp4",
        codec="h264",
        display_long_edge=320,
        display_short_edge=240,
        rotation_degrees=0,
        frame_count=frames,
        duration_ms=frames * 200,
        average_fps=5,
    )
    outcome = await ProcessVideoAnalyzer(executable, manifest, "mp-face-pose-v1", "behavior-v1").analyze(
        prepared, VideoLimits()
    )
    assert outcome.status == expected
    if expected == "unavailable":
        assert outcome.data is None
        assert outcome.error is not None
        assert outcome.error.code == "BEHAVIOR_NOT_MEASURABLE"
        assert outcome.error.retryable is False
    else:
        assert outcome.error is None
        data = BehaviorData.model_validate(outcome.data)
        assert data.metrics["faceVisibleRatio"].value == 0
        assert data.metrics["headFacingRatio"].value is None
        assert data.metrics["headFacingRatio"].unavailable_reason == "NO_SINGLE_FACE"
