"""Pure decisions about real media: container headers, display geometry and decoded timelines.

The adapter runs FFprobe and feeds parsed values here, so every limit rule is testable without binaries.
"""

from __future__ import annotations

import math
import struct
from collections import deque
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Literal

from app.core.common.video.errors import VideoStageError
from app.core.common.video.policy import VideoLimits

HEADER_READ_BYTES = 64 * 1024
MAX_FRAME_COUNT = 216_001

Container = Literal["webm", "mp4"]
MP4_BRANDS = frozenset({"isom", "iso2", "iso3", "iso4", "iso5", "iso6", "mp41", "mp42", "avc1", "dash"})
CODECS: dict[Container, frozenset[str]] = {"webm": frozenset({"vp8", "vp9"}), "mp4": frozenset({"h264"})}
EBML_MAGIC = b"\x1a\x45\xdf\xa3"
EBML_DOCTYPE_ID = 0x4282
SEGMENT_ID = 0x18538067
INFO_ID = 0x1549A966
TIMECODE_SCALE_ID = 0x2AD7B1
DURATION_ID = 0x4489
_LEVEL1_SKIPPABLE = frozenset({0x114D9B74, 0xEC, 0xBF})  # SeekHead, Void, CRC-32
_UNKNOWN_SIZE_BYTES = 8
_FTYP_HEADER = 8
_BRAND = 4


def unsupported() -> VideoStageError:
    return VideoStageError("VIDEO_FORMAT_UNSUPPORTED")


def decode_failed() -> VideoStageError:
    return VideoStageError("VIDEO_DECODE_FAILED")


def limit_exceeded() -> VideoStageError:
    return VideoStageError("VIDEO_LIMIT_EXCEEDED")


# ---------------------------------------------------------------- container headers


def detect_container(header: bytes) -> Container:
    if header.startswith(EBML_MAGIC):
        if _ebml_doctype(header) != "webm":
            raise unsupported()  # generic Matroska/MKV is not WebM
        return "webm"
    if len(header) >= _FTYP_HEADER and header[4:8] == b"ftyp":
        major, compatible = _ftyp_brands(header)
        if major == "qt  " or major.startswith("3g") or "qt  " in compatible:
            raise unsupported()  # QuickTime/MOV and 3GP
        if major in MP4_BRANDS or compatible & MP4_BRANDS:
            return "mp4"
    raise unsupported()


def _read_vint(data: bytes, offset: int, *, keep_marker: bool) -> tuple[int, int]:
    if offset >= len(data):
        raise unsupported()
    first = data[offset]
    length = 1
    mask = 0x80
    while length <= 8 and not first & mask:  # noqa: PLR2004 — EBML VINT maximum width
        mask >>= 1
        length += 1
    if length > 8 or offset + length > len(data):  # noqa: PLR2004
        raise unsupported()
    value = first if keep_marker else first & (mask - 1)
    for byte in data[offset + 1 : offset + length]:
        value = (value << 8) | byte
    return value, offset + length


def _ebml_doctype(header: bytes) -> str | None:
    _, offset = _read_vint(header, 0, keep_marker=True)  # EBML element id
    size, offset = _read_vint(header, offset, keep_marker=False)
    end = offset + size
    if end > len(header):
        raise unsupported()
    while offset < end:
        element_id, offset = _read_vint(header, offset, keep_marker=True)
        element_size, offset = _read_vint(header, offset, keep_marker=False)
        if offset + element_size > end:
            raise unsupported()
        if element_id == EBML_DOCTYPE_ID:
            return header[offset : offset + element_size].rstrip(b"\x00").decode("ascii", errors="replace")
        offset += element_size
    return None


def webm_declared_duration(header: bytes) -> Fraction | None:
    """Segment Info Duration from the bounded header, or None when absent.

    FFprobe would otherwise *estimate* a missing duration from the bitrate (browser MediaRecorder files
    have none), and an estimate must never decide a limit.
    """
    try:
        _, offset = _read_vint(header, 0, keep_marker=True)
        size, offset = _read_vint(header, offset, keep_marker=False)
        offset += size
        segment_id, offset = _read_vint(header, offset, keep_marker=True)
        if segment_id != SEGMENT_ID:
            return None
        _, offset = _read_vint(header, offset, keep_marker=False)  # segment size may be "unknown"
        while offset < len(header):
            element_id, offset = _read_vint(header, offset, keep_marker=True)
            element_size, offset = _read_vint(header, offset, keep_marker=False)
            if element_id == INFO_ID:
                return _info_duration(header[offset : offset + element_size])
            if element_id not in _LEVEL1_SKIPPABLE:
                return None  # clusters/tracks before Info: treat as absent rather than guess
            offset += element_size
    except VideoStageError:
        return None
    return None


def _info_duration(info: bytes) -> Fraction | None:
    scale = 1_000_000
    duration: float | None = None
    offset = 0
    while offset < len(info):
        element_id, offset = _read_vint(info, offset, keep_marker=True)
        element_size, offset = _read_vint(info, offset, keep_marker=False)
        payload = info[offset : offset + element_size]
        if element_id == TIMECODE_SCALE_ID and payload:
            scale = int.from_bytes(payload, "big")
        elif element_id == DURATION_ID and len(payload) in (4, 8):
            duration = struct.unpack(">f" if len(payload) == 4 else ">d", payload)[0]  # noqa: PLR2004
        offset += element_size
    if duration is None:
        return None
    if not math.isfinite(duration) or duration <= 0 or scale <= 0:
        raise decode_failed()
    return Fraction(duration) * scale / 1_000_000_000


def _ftyp_brands(header: bytes) -> tuple[str, frozenset[str]]:
    size = int.from_bytes(header[0:4], "big")
    if size < _FTYP_HEADER + 2 * _BRAND or size > len(header) or (size - _FTYP_HEADER) % _BRAND:
        raise unsupported()
    major = header[8:12].decode("latin-1")
    compatible = frozenset(header[offset : offset + 4].decode("latin-1") for offset in range(16, size, 4))
    return major, compatible


# ---------------------------------------------------------------- stream geometry


def parse_ratio(value: object, *, default: Fraction | None = None) -> Fraction:
    if value in (None, "", "N/A", "0:1", "0/1"):
        if default is None:
            raise decode_failed()
        return default
    text = str(value).replace(":", "/")
    try:
        ratio = Fraction(text)
    except (ValueError, ZeroDivisionError) as exc:
        raise decode_failed() from exc
    if ratio <= 0:
        raise decode_failed()
    return ratio


def parse_positive_int(value: object) -> int:
    if isinstance(value, bool):
        raise decode_failed()
    try:
        number = int(str(value))
    except (TypeError, ValueError) as exc:
        raise decode_failed() from exc
    if number <= 0:
        raise decode_failed()
    return number


def rotation_degrees(stream: dict[str, object]) -> float:
    """Display-matrix rotation first, then the legacy ``rotate`` tag, else 0."""
    raw: object = None
    side_data = stream.get("side_data_list")
    if isinstance(side_data, list):
        for item in side_data:
            if isinstance(item, dict) and "rotation" in item:
                raw = item["rotation"]
                break
    if raw is None:
        tags = stream.get("tags")
        if isinstance(tags, dict):
            raw = tags.get("rotate")
    if raw is None:
        return 0.0
    try:
        angle = float(str(raw))
    except ValueError as exc:
        raise decode_failed() from exc
    if not math.isfinite(angle):
        raise decode_failed()
    return angle


@dataclass(frozen=True)
class DisplayBox:
    long_edge: int
    short_edge: int


def display_box(width: int, height: int, sar: Fraction, rotation: float) -> DisplayBox:
    display_width = Fraction(width) * sar
    display_height = Fraction(height)
    radians = math.radians(rotation)
    # Round away float noise (cos 90° = 6e-17) before taking the axis-aligned bounding box ceiling.
    cos = abs(round(math.cos(radians), 12))
    sin = abs(round(math.sin(radians), 12))
    box_width = math.ceil(round(float(display_width) * cos + float(display_height) * sin, 6))
    box_height = math.ceil(round(float(display_width) * sin + float(display_height) * cos, 6))
    if box_width <= 0 or box_height <= 0:
        raise decode_failed()
    return DisplayBox(long_edge=max(box_width, box_height), short_edge=min(box_width, box_height))


def check_display_limits(box: DisplayBox, limits: VideoLimits) -> None:
    if box.long_edge > limits.max_long_edge or box.short_edge > limits.max_short_edge:
        raise limit_exceeded()


# ---------------------------------------------------------------- decoded timeline


@dataclass
class TimelineAccumulator:
    """Consumes decoded frames in presentation order; aborts early only on a definite violation.

    Frame-rate rule: no 1-second window ``[pts, pts + 1s)`` may hold more than ``max_fps`` frames, and the
    whole-file average must not exceed it. A per-gap minimum would reject real browser MediaRecorder files,
    whose capture jitter produces occasional 2-4 ms gaps at ~20-30 fps; a sustained 61 fps stream still
    puts 61 frames into one second and is rejected.
    """

    time_base: Fraction
    limits: VideoLimits
    coded_width: int
    coded_height: int
    sar: Fraction
    rotation: float
    count: int = 0
    first_pts: int | None = None
    last_pts: int | None = None
    last_duration: int | None = None
    last_positive_gap: int | None = None
    window: deque[int] = field(default_factory=deque)

    def add(self, pts: int | None, duration: int | None, width: int, height: int) -> None:
        self.count += 1
        if self.count > MAX_FRAME_COUNT:
            raise limit_exceeded()
        if pts is None:
            raise decode_failed()
        if (width, height) != (self.coded_width, self.coded_height):
            check_display_limits(display_box(width, height, self.sar, self.rotation), self.limits)
        tick = self.time_base
        if self.last_pts is not None:
            if pts <= self.last_pts:
                raise decode_failed()  # presentation timestamps must strictly increase
            self.last_positive_gap = pts - self.last_pts
        else:
            self.first_pts = pts
        self.window.append(pts)
        while (pts - self.window[0]) * tick >= 1:
            self.window.popleft()
        if len(self.window) > self.limits.max_fps:
            raise limit_exceeded()
        self.last_pts = pts
        self.last_duration = duration if duration is not None and duration > 0 else None
        if self.first_pts is not None and (pts - self.first_pts) * tick * 1000 > self.limits.max_duration_ms:
            raise limit_exceeded()

    def finish(self, declared_duration: Fraction | None) -> DecodedTimeline:
        if self.count == 0 or self.first_pts is None or self.last_pts is None:
            raise decode_failed()
        tick = self.time_base
        span = (self.last_pts - self.first_pts) * tick
        if self.last_duration is not None:
            last = self.last_duration * tick
        elif declared_duration is not None and declared_duration > span:
            last = declared_duration - span
        elif self.last_positive_gap is not None:
            last = self.last_positive_gap * tick
        else:
            raise decode_failed()  # single frame with no definable duration
        decoded = span + last
        if self.count >= 2:  # noqa: PLR2004
            rate = Fraction(self.count - 1) / (span + tick)
            if rate > self.limits.max_fps:
                raise limit_exceeded()
        else:
            rate = 1 / last
        governing = max(decoded, declared_duration) if declared_duration is not None else decoded
        if governing * 1000 > self.limits.max_duration_ms:
            raise limit_exceeded()
        return DecodedTimeline(
            frame_count=self.count,
            duration=decoded,
            duration_ms=round(decoded * 1000),
            average_fps=float(rate),
        )


@dataclass(frozen=True)
class DecodedTimeline:
    frame_count: int
    duration: Fraction
    duration_ms: int
    average_fps: float
