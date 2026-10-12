"""Local model bridge. Executes only an operator-configured binary and existing model file.

The model owner supplies the executable and behavior schema; this adapter invents no measurements.
The executable accepts --model-path, --model-version and --input-json (PreparedVideo JSON), and
returns one AnalysisOutcome JSON on stdout. ready/partial data must identify its schema and model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from app.core.common.video.dto import AnalysisOutcome, PreparedVideo
from app.core.common.video.errors import VideoStageError
from app.core.common.video.policy import VideoLimits
from app.outbound.adapters.video.media_process import ProcessLimitError, ProcessLimits, run_bounded

OUTPUT_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ProcessVideoAnalyzer:
    executable: Path
    model_path: Path
    model_version: str
    data_schema_version: str

    async def analyze(self, prepared: PreparedVideo, limits: VideoLimits) -> AnalysisOutcome:
        try:
            result = await run_bounded(
                str(self.executable),
                [
                    "--model-path",
                    str(self.model_path),
                    "--model-version",
                    self.model_version,
                    "--input-json",
                    json.dumps(prepared.model_dump(), allow_nan=False),
                ],
                ProcessLimits(
                    # Both limits come from the accepted job's policy snapshot, never from today's worker
                    # settings. The enclosing processor applies the same timeout; cancellation reaps the child.
                    timeout_seconds=limits.analyze_timeout_seconds,
                    max_stdout_bytes=OUTPUT_BYTES,
                    max_line_bytes=OUTPUT_BYTES,
                    max_rss_bytes=limits.max_process_rss_bytes,
                ),
            )
        except ProcessLimitError as exc:
            # Whichever timer fires first, a timeout is handled exactly like the processor's own timeout.
            if exc.kind == "timeout":
                raise VideoStageError("PROCESSING_TIMEOUT", stage_retry=True) from exc
            raise VideoStageError("INTERNAL_ERROR") from exc
        if result.returncode != 0:
            raise VideoStageError("INTERNAL_ERROR")
        try:
            outcome = AnalysisOutcome.model_validate_json(result.stdout)
        except (ValueError, ValidationError) as exc:
            raise VideoStageError("INTERNAL_ERROR") from exc
        if outcome.status in ("ready", "partial"):
            data = outcome.data
            if (
                not isinstance(data, dict)
                or data.get("schemaVersion") != self.data_schema_version
                or data.get("modelVersion") != self.model_version
            ):
                raise VideoStageError("INTERNAL_ERROR")
        return outcome
