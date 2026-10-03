"""Run with ``python -m app.main.video_worker [--once] [--lane io|cpu|callback ...]``.

Run separate processes per lane in production (an hour-long decode must not delay callbacks). Settings
must match the API process: same database_path, artifact_root and security environment.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
from collections.abc import Sequence
from dataclasses import dataclass

import httpx

from app.core.commands.deliver_video_callback import DeliverVideoCallback
from app.core.commands.process_video_task import ProcessVideoTask
from app.core.common.video.ports import Clock, VideoAnalyzer, VideoMediaBackend
from app.main.config import CallbackSecuritySettings, load_callback_security_settings
from app.main.log_redaction import install_log_redaction
from app.main.video_bootstrap import video_repository, video_security
from app.main.video_config import VideoSettings
from app.outbound.adapters.httpx_callback_transport import HttpxCallbackTransport
from app.outbound.adapters.video.analyzer import UnconfiguredVideoAnalyzer
from app.outbound.adapters.video.media_backend import LocalVideoMediaBackend
from app.outbound.adapters.video.process_analyzer import ProcessVideoAnalyzer

LANES = ("io", "cpu", "callback")
logger = logging.getLogger(__name__)


class WorkerBootError(RuntimeError):
    """Misconfiguration found before any job was claimed. Never reported as a damaged file."""


@dataclass
class VideoWorker:
    processor: ProcessVideoTask | None
    deliverer: DeliverVideoCallback | None

    async def run_once(self) -> bool:
        processed = False
        # Deliver finished results before taking more input.
        if self.deliverer is not None:
            processed = await self.deliverer.run_once() or processed
        if self.processor is not None:
            processed = await self.processor.run_once() or processed
        return processed


def configured_analyzer(video: VideoSettings) -> VideoAnalyzer:
    settings = video.analyzer
    if settings.backend == "unconfigured":
        return UnconfiguredVideoAnalyzer()
    executable, model = settings.executable, settings.model_path
    if executable is None or not executable.is_file() or not os.access(executable, os.X_OK):
        raise WorkerBootError("analyzer executable is unavailable")
    if model is None or not model.is_file() or not os.access(model, os.R_OK):
        raise WorkerBootError("analyzer model file is unavailable")
    if settings.model_version is None or settings.data_schema_version is None:
        raise WorkerBootError("analyzer versions are required")
    logger.info(
        "video.analyzer.configured model_version=%s data_schema_version=%s",
        settings.model_version,
        settings.data_schema_version,
    )
    return ProcessVideoAnalyzer(
        executable, model, settings.model_version, settings.data_schema_version, video.policy.max_process_rss_bytes
    )


def build_worker(
    lanes: Sequence[str] | None = None,
    *,
    callback_settings: CallbackSecuritySettings | None = None,
    video_settings: VideoSettings | None = None,
    analyzer: VideoAnalyzer | None = None,
    media: VideoMediaBackend | None = None,
    source_transport: httpx.AsyncBaseTransport | None = None,
    callback_transport: httpx.AsyncBaseTransport | None = None,
    clock: Clock | None = None,
    ffprobe: str = "ffprobe",
) -> VideoWorker:
    # 1) Validate security/settings before touching the database (production needs the callback token).
    callback = callback_settings if callback_settings is not None else load_callback_security_settings()
    video = video_settings if video_settings is not None else VideoSettings()
    if callback.ENVIRONMENT == "production" and (lanes is None or len(lanes) != 1):
        raise WorkerBootError("production workers require one explicit --lane")
    lanes = lanes if lanes is not None else LANES
    unknown = set(lanes) - set(LANES)
    if unknown or not lanes:
        raise WorkerBootError("unknown or empty lane selection")
    # 2) CPU lanes need FFprobe; a missing binary is an environment failure, checked before claiming.
    if "cpu" in lanes and media is None and shutil.which(ffprobe) is None:
        raise WorkerBootError("ffprobe is required for the cpu lane")
    selected_analyzer = (
        analyzer
        if analyzer is not None
        else (configured_analyzer(video) if "cpu" in lanes else UnconfiguredVideoAnalyzer())
    )
    security = video_security(callback, video)
    repository = video_repository(video, clock)
    stage_lanes = tuple(lane for lane in lanes if lane in ("io", "cpu"))
    processor = None
    if stage_lanes:
        processor = ProcessVideoTask(
            repository,
            media or LocalVideoMediaBackend(security, ffprobe=ffprobe, transport=source_transport),
            selected_analyzer,
            video.capacities(stage_lanes),
            video.lease_seconds,
        )
    deliverer = None
    if "callback" in lanes:
        deliverer = DeliverVideoCallback(
            repository,
            HttpxCallbackTransport(security, callback_transport),
            video.callback_concurrency,
            video.lease_seconds,
        )
    return VideoWorker(processor=processor, deliverer=deliverer)


async def run(lanes: Sequence[str] | None, *, once: bool = False, idle_seconds: float = 1.0) -> None:
    worker = build_worker(lanes)
    while True:
        processed = await worker.run_once()
        if once:
            return
        if not processed:
            await asyncio.sleep(idle_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--lane", action="append", choices=LANES, help="repeatable; default: all lanes")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    install_log_redaction()
    asyncio.run(run(tuple(args.lane) if args.lane else None, once=args.once))


if __name__ == "__main__":
    main()
