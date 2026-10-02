from __future__ import annotations

from app.core.common.video.dto import AnalysisOutcome, PreparedVideo, public_error


class UnconfiguredVideoAnalyzer:
    """The only analyzer the real service binds today.

    No behaviour model exists yet, so a valid file honestly ends as ``unavailable`` with
    ``ANALYZER_NOT_CONFIGURED`` instead of a fabricated success. Test fakes live under ``tests/``.
    """

    async def analyze(self, prepared: PreparedVideo) -> AnalysisOutcome:
        return AnalysisOutcome(status="unavailable", data=None, error=public_error("ANALYZER_NOT_CONFIGURED"))
