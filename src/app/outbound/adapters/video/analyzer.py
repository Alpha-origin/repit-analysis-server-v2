from __future__ import annotations

from app.core.common.video.dto import AnalysisOutcome, PreparedVideo, public_error
from app.core.common.video.policy import VideoLimits


class UnconfiguredVideoAnalyzer:
    """The default analyzer while the model and behavior schema remain undecided.

    No behaviour model exists yet, so a valid file honestly ends as ``unavailable`` with
    ``ANALYZER_NOT_CONFIGURED`` instead of a fabricated success. Test fakes live under ``tests/``.
    """

    async def analyze(self, prepared: PreparedVideo, limits: VideoLimits) -> AnalysisOutcome:
        return AnalysisOutcome(status="unavailable", data=None, error=public_error("ANALYZER_NOT_CONFIGURED"))
