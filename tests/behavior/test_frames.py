from __future__ import annotations

import hashlib
from fractions import Fraction
from pathlib import Path
from typing import Literal

import pytest

from app.core.common.video.dto import PreparedVideo
from app.outbound.adapters.behavior.frames import _display_frame, samples

CODEC_NAMES: dict[str, Literal["h264", "vp8", "vp9"]] = {"libx264": "h264", "libvpx": "vp8", "libvpx-vp9": "vp9"}

av = pytest.importorskip("av")
np = pytest.importorskip("numpy")


def video(
    tmp_path: Path, timestamps: list[int], *, codec: str = "libx264", extension: Literal["webm", "mp4"] = "mp4"
) -> PreparedVideo:
    path = tmp_path / f"sample.{extension}"
    with av.open(str(path), mode="w") as output:
        stream = output.add_stream(codec, rate=1000)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        stream.time_base = Fraction(1, 1000)
        stream.codec_context.time_base = Fraction(1, 1000)
        for pts in timestamps:
            frame = av.VideoFrame.from_ndarray(np.zeros((48, 64, 3), dtype=np.uint8), format="rgb24")
            frame.pts, frame.time_base = pts, Fraction(1, 1000)
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    content = path.read_bytes()
    return PreparedVideo(
        video_id="sample",
        path=str(path),
        sha256=hashlib.sha256(content).hexdigest(),
        bytes=len(content),
        container=extension,
        codec=CODEC_NAMES[codec],
        display_long_edge=64,
        display_short_edge=48,
        rotation_degrees=0,
        frame_count=len(timestamps),
        duration_ms=timestamps[-1] - timestamps[0] + 200,
        average_fps=5,
    )


def test_vfr_sampling_uses_pts_and_does_not_fill_empty_slots(tmp_path: Path) -> None:
    prepared = video(tmp_path, [0, 40, 199, 200, 401, 850, 1000])
    result = list(samples(prepared))
    assert result[0].rgb.shape == (48, 64, 3)
    assert [s.slot for s in result] == [0, 1, 2, 4, 5]
    assert [s.timestamp_ms for s in result] == [0, 200, 401, 850, 1000]


@pytest.mark.parametrize(("codec", "extension"), [("libvpx", "webm"), ("libvpx-vp9", "webm")])
def test_webm_pts_sampling(tmp_path: Path, codec: str, extension: Literal["webm", "mp4"]) -> None:
    result = list(samples(video(tmp_path, [0, 100, 200, 400, 600], codec=codec, extension=extension)))
    assert [s.slot for s in result] == [0, 1, 2, 3]


def test_rotation_sar_and_resize_preserve_display_coordinates() -> None:
    rgb = np.zeros((200, 400, 3), dtype=np.uint8)
    rgb[:100, :200, 0] = 255
    transformed = _display_frame(rgb, Fraction(2), 90)
    assert transformed.shape == (640, 160, 3)
    assert transformed[-40, 40, 0] > 200


def test_validated_stream_metadata_is_not_silently_replaced(tmp_path: Path) -> None:
    prepared = video(tmp_path, [0, 200, 400])
    with pytest.raises(ValueError, match="stream changed"):
        list(samples(prepared.model_copy(update={"codec": "vp9"})))
