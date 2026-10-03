"""Run with ``python -m app.main.video_cleanup [--once] [--interval 3600] [--batch 100]``.

Reclaims local media (24h after the job ends), turns expired results into tombstones (90 days) and
removes tombstones (+90 days). GET/POST already apply expiry logically, so a late or missing run only
delays storage reclamation. Concurrent runs are safe. No cron/system service is installed by this repo.
"""

from __future__ import annotations

import argparse
import logging
import time

from app.core.commands.cleanup_video_jobs import CleanupVideoJobs
from app.core.common.video.ports import Clock
from app.main.config import CallbackSecuritySettings, load_callback_security_settings
from app.main.log_redaction import install_log_redaction
from app.main.video_bootstrap import video_repository
from app.main.video_config import VideoSettings


def build_cleanup(
    *,
    callback_settings: CallbackSecuritySettings | None = None,
    video_settings: VideoSettings | None = None,
    clock: Clock | None = None,
    batch_size: int = 100,
) -> CleanupVideoJobs:
    # Same boot validation as the API/workers, before the database is opened.
    if callback_settings is None:
        load_callback_security_settings()
    video = video_settings if video_settings is not None else VideoSettings()
    if batch_size < 1:
        raise ValueError("batch size must be positive")
    return CleanupVideoJobs(video_repository(video, clock), batch_size)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=3600, help="seconds between passes (default 3600)")
    parser.add_argument("--batch", type=int, default=100, help="jobs/artifacts per step (default 100)")
    parser.add_argument("--max-passes", type=int, default=1, help="bounded batches per invocation")
    args = parser.parse_args()
    if args.interval < 1 or args.max_passes < 1:
        parser.error("interval and max-passes must be positive")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    install_log_redaction()
    cleanup = build_cleanup(batch_size=args.batch)
    while True:
        for _ in range(args.max_passes):
            stats = cleanup.run_once()
            if max(stats.expired_jobs, stats.purged_tombstones, stats.collected_artifacts) < args.batch:
                break
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
