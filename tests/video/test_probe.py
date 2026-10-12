from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest

from app.core.common.video.errors import VideoStageError
from app.core.common.video.media_validation import (
    check_display_limits,
    detect_container,
    display_box,
    parse_positive_int,
    parse_ratio,
    rotation_degrees,
)
from app.core.common.video.policy import VideoLimits
from app.outbound.adapters.video.media_backend import LocalVideoMediaBackend
from tests.video.helpers import security
from tests.video.media_fixtures import ffmpeg, lavfi_video, remux, require_media_tools


def ebml(doctype: bytes) -> bytes:
    body = b"\x42\x82" + bytes([0x80 | len(doctype)]) + doctype
    return b"\x1a\x45\xdf\xa3" + bytes([0x80 | len(body)]) + body + b"\x18\x53\x80\x67"


def ftyp(major: bytes, *compatible: bytes) -> bytes:
    body = major + b"\x00\x00\x02\x00" + b"".join(compatible)
    return (8 + len(body)).to_bytes(4, "big") + b"ftyp" + body


def _code(callable_: object, *args: object) -> str:
    with pytest.raises(VideoStageError) as caught:
        callable_(*args)  # type: ignore[operator]
    return caught.value.code


@pytest.mark.asyncio
async def test_supported_container_codec_and_rotated_dimensions(tmp_path: Path) -> None:
    assert detect_container(ebml(b"webm")) == "webm"
    assert detect_container(ftyp(b"isom", b"isom", b"iso2", b"avc1", b"mp41")) == "mp4"
    assert detect_container(ftyp(b"mp42", b"mp42")) == "mp4"
    limits = VideoLimits()
    assert display_box(1920, 1080, Fraction(1), 90.0).long_edge == 1920
    assert display_box(1080, 1920, Fraction(1), -90.0).short_edge == 1080
    # 45 degrees turns 1280x720 into a ~1414 square bounding box: exceeds the short edge.
    assert _code(check_display_limits, display_box(1280, 720, Fraction(1), 45.0), limits) == "VIDEO_LIMIT_EXCEEDED"
    # Anamorphic SAR widens the display rectangle.
    assert display_box(1440, 1080, Fraction(4, 3), 0.0).long_edge == 1920
    assert _code(check_display_limits, display_box(1440, 1080, Fraction(3, 2), 0.0), limits) == "VIDEO_LIMIT_EXCEEDED"
    assert rotation_degrees({"side_data_list": [{"rotation": -90}], "tags": {"rotate": "180"}}) == -90.0
    assert rotation_degrees({"tags": {"rotate": "180"}}) == 180.0
    assert rotation_degrees({}) == 0.0

    require_media_tools()
    backend = LocalVideoMediaBackend(security())
    landscape = lavfi_video(tmp_path / "hd.mp4", codec="libx264", size="1920x1080", seconds=0.2)
    rotated = remux(landscape, tmp_path / "rotated.mp4")
    ffmpeg("-display_rotation", "90", "-i", str(landscape), "-c", "copy", str(tmp_path / "rotated90.mp4"))
    for path in (landscape, rotated, tmp_path / "rotated90.mp4"):
        inspected = await backend.inspect(str(path), limits)
        assert (inspected["displayLongEdge"], inspected["displayShortEdge"]) == (1920, 1080)
    assert (await backend.inspect(str(tmp_path / "rotated90.mp4"), limits))["rotationDegrees"] in (90.0, -90.0)
    webm = lavfi_video(tmp_path / "vp9.webm", codec="libvpx-vp9", audio=True)  # audio is ignored
    assert (await backend.inspect(str(webm), limits))["codec"] == "vp9"


@pytest.mark.parametrize(
    "header",
    [
        ebml(b"matroska"),  # generic MKV is not WebM
        ftyp(b"qt  ", b"qt  "),  # QuickTime/MOV
        ftyp(b"3gp4", b"isom", b"3gp4"),  # 3GP
        ftyp(b"isom", b"qt  "),
        ftyp(b"heic", b"mif1"),
        b"RIFF\x00\x00\x00\x00AVI ",
        b"",
        b"\x1a\x45\xdf\xa3\xff",  # truncated EBML
    ],
)
def test_container_headers_rejected(header: bytes) -> None:
    assert _code(detect_container, header) == "VIDEO_FORMAT_UNSUPPORTED"


@pytest.mark.asyncio
async def test_mkv_mov_wrong_codec_and_streams_rejected(tmp_path: Path) -> None:
    require_media_tools()
    backend = LocalVideoMediaBackend(security())
    limits = VideoLimits()
    source = lavfi_video(tmp_path / "source.webm", codec="libvpx")
    h264 = lavfi_video(tmp_path / "source.mp4", codec="libx264")
    rejected = {
        "mkv": remux(source, tmp_path / "video.mkv"),
        "mov": remux(h264, tmp_path / "video.mov"),
        "3gp": remux(h264, tmp_path / "video.3gp"),
        "vp9-in-mp4": lavfi_video(tmp_path / "vp9.mp4", codec="libvpx-vp9"),
        "mpeg4-in-mp4": lavfi_video(tmp_path / "mpeg4.mp4", codec="mpeg4"),
    }
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=440", "-t", "1", "-c:a", "libopus", str(tmp_path / "audio.webm"))
    rejected["audio-only"] = tmp_path / "audio.webm"
    ffmpeg("-i", str(h264), "-i", str(h264), "-map", "0:v", "-map", "1:v", "-c", "copy", str(tmp_path / "two.mp4"))
    rejected["two-video-streams"] = tmp_path / "two.mp4"
    for name, path in rejected.items():
        with pytest.raises(VideoStageError) as caught:
            await backend.inspect(str(path), limits)
        assert caught.value.code == "VIDEO_FORMAT_UNSUPPORTED", name
    too_big = lavfi_video(tmp_path / "big.webm", codec="libvpx", size="2000x1000", seconds=0.2)
    with pytest.raises(VideoStageError) as caught:
        await backend.inspect(str(too_big), limits)
    assert caught.value.code == "VIDEO_LIMIT_EXCEEDED"


@pytest.mark.parametrize(
    "case",
    [
        (parse_ratio, "N/A"),
        (parse_ratio, "0/0"),
        (parse_ratio, "-1/2"),
        (parse_ratio, "abc"),
        (parse_positive_int, 0),
        (parse_positive_int, -5),
        (parse_positive_int, "nan"),
        (parse_positive_int, None),
        (parse_positive_int, True),
        (rotation_degrees, {"side_data_list": [{"rotation": "nan"}]}),
        (rotation_degrees, {"tags": {"rotate": "inf"}}),
        (rotation_degrees, {"tags": {"rotate": "ninety"}}),
    ],
)
def test_malformed_metadata_not_reported_ready(case: tuple[object, object]) -> None:
    function, value = case
    assert _code(function, value) == "VIDEO_DECODE_FAILED"


@pytest.mark.asyncio
async def test_mime_or_extension_cannot_bypass_real_checks(tmp_path: Path) -> None:
    require_media_tools()
    backend = LocalVideoMediaBackend(security())
    fake = tmp_path / "video.webm"
    fake.write_bytes(b"this is not a video at all" * 100)
    with pytest.raises(VideoStageError) as caught:
        await backend.inspect(str(fake), VideoLimits())
    assert caught.value.code == "VIDEO_FORMAT_UNSUPPORTED"
