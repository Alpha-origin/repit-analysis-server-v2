from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
from collections.abc import Callable
from fractions import Fraction
from http import HTTPStatus
from pathlib import Path
from typing import Any

import httpx

from app.core.common.security.ports import RuntimeSecurityProvider
from app.core.common.security.url_policy import UrlPolicyError, check_source_url
from app.core.common.video.dto import VideoRequest
from app.core.common.video.errors import VideoStageError
from app.core.common.video.media_validation import (
    CODECS,
    HEADER_READ_BYTES,
    TimelineAccumulator,
    check_display_limits,
    decode_failed,
    detect_container,
    display_box,
    limit_exceeded,
    parse_positive_int,
    parse_ratio,
    rotation_degrees,
    unsupported,
    webm_declared_duration,
)
from app.core.common.video.policy import VideoLimits
from app.core.common.video.ports import ArtifactReservation
from app.outbound.adapters.video.media_process import ProcessLimitError, ProcessLimits, ProcessResult, run_bounded

CHUNK_BYTES = 64 * 1024
METADATA_STDOUT_BYTES = 1024 * 1024
FRAME_LINE_BYTES = 8 * 1024
FRAMES_STDOUT_BYTES = 64 * 1024 * 1024
DEMUXERS = {"webm": "matroska", "mp4": "mov"}
_TRANSIENT_STATUS = {HTTPStatus.TOO_MANY_REQUESTS, HTTPStatus.REQUEST_TIMEOUT}


class LocalVideoMediaBackend:
    """Downloads with HTTPX and checks real media with FFprobe; never trusts MIME types or extensions."""

    def __init__(
        self,
        security: RuntimeSecurityProvider,
        *,
        ffprobe: str = "ffprobe",
        transport: httpx.AsyncBaseTransport | None = None,
        rss_interval_seconds: float = 1.0,
    ) -> None:
        self._security = security
        self._ffprobe = ffprobe
        self._transport = transport
        self._rss_interval = rss_interval_seconds

    # ------------------------------------------------------------------ source

    async def download(
        self, request: VideoRequest, limits: VideoLimits, reservation: ArtifactReservation
    ) -> dict[str, Any]:
        url = request.video.file_url
        snapshot = self._security.current()
        try:
            # Accepted source hosts + *current* emergency blocks.
            check_source_url(url, frozenset(limits.source_hosts), snapshot.blocked_source_hosts)
        except UrlPolicyError as exc:
            raise VideoStageError("SOURCE_ACCESS_DENIED_OR_EXPIRED") from exc
        temporary = Path(reservation.temp_path)
        if shutil.disk_usage(temporary.parent).free < limits.min_free_disk_bytes:
            raise VideoStageError("INTERNAL_ERROR", stage_retry=True)
        expected = request.video.file_size
        try:
            async with asyncio.timeout(limits.download_timeout_seconds):
                size, sha256 = await self._stream(url, temporary, expected, limits)
        except TimeoutError as exc:
            _discard(temporary)
            raise VideoStageError("PROCESSING_TIMEOUT") from exc
        except BaseException:
            # Failed or cancelled downloads clean only their own temporary file.
            _discard(temporary)
            raise
        if size != expected:
            _discard(temporary)
            raise VideoStageError("SOURCE_SIZE_MISMATCH")
        return {"path": reservation.final_path, "sha256": sha256, "bytes": size}

    async def _stream(self, url: str, temporary: Path, expected: int, limits: VideoLimits) -> tuple[int, str]:
        checksum = hashlib.sha256()
        size = 0
        try:
            async with (
                httpx.AsyncClient(
                    timeout=30,
                    transport=self._transport,
                    follow_redirects=False,
                    trust_env=False,
                    verify=True,
                    headers={"Accept-Encoding": "identity"},
                ) as client,
                client.stream("GET", url) as response,
            ):
                _check_status(response.status_code)
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise VideoStageError("SOURCE_DOWNLOAD_FAILED")
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    # Raw bytes, never decoded; Content-Length alone never establishes success.
                    async for block in response.aiter_raw(CHUNK_BYTES):
                        size += len(block)
                        if size > expected:
                            raise VideoStageError("SOURCE_SIZE_MISMATCH")
                        if size > limits.max_bytes or size > limits.max_job_disk_bytes:
                            raise limit_exceeded()
                        stream.write(block)
                        checksum.update(block)
        except httpx.HTTPError as exc:
            raise VideoStageError("SOURCE_DOWNLOAD_FAILED", stage_retry=True) from exc
        return size, checksum.hexdigest()

    # ------------------------------------------------------------------ inspect

    async def inspect(self, path: str, limits: VideoLimits) -> dict[str, Any]:
        header = await asyncio.to_thread(_read_header, Path(path))
        container = detect_container(header)
        stdout = await self._probe(
            [
                *self._input_options(container),
                "-select_streams",
                "V",
                "-show_entries",
                "format=duration:stream=index,codec_type,codec_name,width,height,sample_aspect_ratio,duration,"
                "time_base,r_frame_rate,avg_frame_rate:stream_tags=rotate:stream_side_data=rotation",
                "-of",
                "json",
                path,
            ],
            ProcessLimits(
                timeout_seconds=limits.probe_timeout_seconds,
                max_stdout_bytes=METADATA_STDOUT_BYTES,
                max_line_bytes=METADATA_STDOUT_BYTES,
                max_rss_bytes=limits.max_process_rss_bytes,
                rss_interval_seconds=self._rss_interval,
            ),
        )
        try:
            data = json.loads(stdout)
            streams = [item for item in data.get("streams", []) if item.get("codec_type") == "video"]
        except (ValueError, AttributeError) as exc:
            raise decode_failed() from exc
        if len(streams) != 1:
            raise unsupported()  # exactly one ordinary video stream (attached pictures excluded by "V")
        stream = streams[0]
        codec = str(stream.get("codec_name", ""))
        if codec not in CODECS[container]:
            raise unsupported()
        width = parse_positive_int(stream.get("width"))
        height = parse_positive_int(stream.get("height"))
        sar = parse_ratio(stream.get("sample_aspect_ratio"), default=Fraction(1))
        rotation = rotation_degrees(stream)
        box = display_box(width, height, sar, rotation)
        check_display_limits(box, limits)
        time_base = parse_ratio(stream.get("time_base"))
        if container == "webm":
            declared = webm_declared_duration(header)
        else:
            declared = _declared_duration(stream.get("duration"), data.get("format", {}).get("duration"))
        if declared is not None and declared * 1000 > limits.max_duration_ms:
            raise limit_exceeded()
        return {
            "container": container,
            "codec": codec,
            "streamIndex": int(stream.get("index", 0)),
            "codedWidth": width,
            "codedHeight": height,
            "sampleAspectRatio": str(sar),
            "rotationDegrees": rotation,
            "displayLongEdge": box.long_edge,
            "displayShortEdge": box.short_edge,
            "timeBase": str(time_base),
            "declaredDuration": str(declared) if declared is not None else None,
        }

    # ------------------------------------------------------------------ validate

    async def validate(self, path: str, inspected: dict[str, Any], limits: VideoLimits) -> dict[str, Any]:
        accumulator = TimelineAccumulator(
            time_base=Fraction(inspected["timeBase"]),
            limits=limits,
            coded_width=int(inspected["codedWidth"]),
            coded_height=int(inspected["codedHeight"]),
            sar=Fraction(inspected["sampleAspectRatio"]),
            rotation=float(inspected["rotationDegrees"]),
        )

        def on_line(line: bytes) -> None:
            if line:
                pts, duration, width, height = _parse_frame(line)
                accumulator.add(pts, duration, width, height)

        result = await self._run(
            [
                *self._input_options(inspected["container"]),
                "-err_detect",
                "explode",
                "-threads",
                "1",
                "-select_streams",
                "V",
                "-show_frames",
                "-show_entries",
                "frame=best_effort_timestamp,duration,pkt_duration,width,height",
                "-of",
                "compact=p=0",
                path,
            ],
            ProcessLimits(
                timeout_seconds=limits.decode_timeout_seconds,
                max_stdout_bytes=FRAMES_STDOUT_BYTES,
                max_line_bytes=FRAME_LINE_BYTES,
                max_rss_bytes=limits.max_process_rss_bytes,
                rss_interval_seconds=self._rss_interval,
            ),
            on_line=on_line,
        )
        # A supported file that made the decoder complain is corrupt, even with exit status 0.
        if result.returncode != 0 or result.stderr_bytes:
            raise decode_failed()
        declared = inspected.get("declaredDuration")
        timeline = accumulator.finish(Fraction(declared) if declared else None)
        return {
            "durationMs": timeline.duration_ms,
            "frameCount": timeline.frame_count,
            "averageFps": timeline.average_fps,
        }

    # ------------------------------------------------------------------ ffprobe plumbing

    @staticmethod
    def _input_options(container: str) -> list[str]:
        options = [
            "-v",
            "error",
            "-protocol_whitelist",
            "file",
            "-format_whitelist",
            "matroska,webm,mov",
            "-f",
            DEMUXERS[container],
        ]
        if container == "mp4":
            options += ["-enable_drefs", "0"]  # never open external data references
        return options

    async def _probe(self, arguments: list[str], limits: ProcessLimits) -> bytes:
        result = await self._run(arguments, limits)
        if result.returncode != 0 or result.stderr_bytes:
            raise decode_failed()
        return result.stdout

    async def _run(
        self, arguments: list[str], limits: ProcessLimits, on_line: Callable[[bytes], None] | None = None
    ) -> ProcessResult:
        try:
            return await run_bounded(self._ffprobe, arguments, limits, on_line=on_line)
        except ProcessLimitError as exc:
            if exc.kind == "timeout":
                raise VideoStageError("PROCESSING_TIMEOUT") from exc
            if exc.kind == "missing_binary":
                # Environment problem, never reported as a damaged file.
                raise VideoStageError("INTERNAL_ERROR") from exc
            raise limit_exceeded() from exc


def _check_status(status: int) -> None:
    if status == HTTPStatus.OK:
        return
    if status == HTTPStatus.FORBIDDEN:
        # 403 does not prove expiry; a new requestId with a fresh URL is the recovery path.
        raise VideoStageError("SOURCE_ACCESS_DENIED_OR_EXPIRED")
    if status == HTTPStatus.NOT_FOUND:
        raise VideoStageError("SOURCE_NOT_FOUND")
    transient = status in _TRANSIENT_STATUS or status >= HTTPStatus.INTERNAL_SERVER_ERROR
    raise VideoStageError("SOURCE_DOWNLOAD_FAILED", stage_retry=transient)


def _discard(path: Path) -> None:
    path.unlink(missing_ok=True)


def _read_header(path: Path) -> bytes:
    with path.open("rb") as stream:
        return stream.read(HEADER_READ_BYTES)


def _declared_duration(*values: object) -> Fraction | None:
    for value in values:
        if value in (None, "N/A", ""):
            continue
        try:
            duration = Fraction(str(value))
        except (ValueError, ZeroDivisionError) as exc:
            raise decode_failed() from exc
        if duration <= 0:
            raise decode_failed()
        return duration
    return None


def _parse_frame(line: bytes) -> tuple[int | None, int | None, int, int]:
    fields: dict[str, str] = {}
    for item in line.decode("ascii", errors="replace").split("|"):
        key, separator, value = item.partition("=")
        if separator:
            fields[key] = value
    pts = _optional_int(fields.get("best_effort_timestamp"))
    # FFprobe versions differ: newer ones print ``duration``, older ones ``pkt_duration``.
    duration = _optional_int(fields.get("duration"))
    if duration is None:
        duration = _optional_int(fields.get("pkt_duration"))
    return pts, duration, parse_positive_int(fields.get("width")), parse_positive_int(fields.get("height"))


def _optional_int(value: str | None) -> int | None:
    if value is None or value in ("", "N/A"):
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise decode_failed() from exc
