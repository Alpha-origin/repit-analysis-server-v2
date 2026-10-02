from __future__ import annotations

import logging

from app.core.common.video.ports import CleanupStats, RetentionRepository

logger = logging.getLogger(__name__)


class CleanupVideoJobs:
    """One bounded retention pass. Safe to run concurrently and repeatedly.

    Logical expiry is already enforced by GET/POST; this only reclaims storage. Order matters:
    local media first (24h), then result -> tombstone (90d), then tombstone removal (+90d).
    """

    def __init__(self, repository: RetentionRepository, batch_size: int = 100) -> None:
        self.repository = repository
        self.batch_size = batch_size

    def run_once(self) -> CleanupStats:
        collected = 0
        errors = 0
        for candidate in self.repository.artifact_gc_candidates(self.batch_size):
            try:
                self.repository.remove_artifact_files(candidate)
            except OSError:
                # Leave the manifest in place; a later pass retries. Paths are never logged.
                errors += 1
                continue
            if self.repository.mark_artifact_collected(candidate.id):
                collected += 1
        stats = CleanupStats(
            collected_artifacts=collected,
            artifact_errors=errors,
            expired_jobs=self.repository.expire_jobs(self.batch_size),
            purged_tombstones=self.repository.purge_tombstones(self.batch_size),
        )
        logger.info(
            "video.cleanup.pass artifacts=%d artifact_errors=%d expired_jobs=%d purged_tombstones=%d",
            stats.collected_artifacts,
            stats.artifact_errors,
            stats.expired_jobs,
            stats.purged_tombstones,
        )
        return stats
