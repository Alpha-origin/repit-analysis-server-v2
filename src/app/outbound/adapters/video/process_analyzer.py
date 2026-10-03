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
from app.core.common.video.errors import PublicErrorCode, VideoStageError
from app.outbound.adapters.video.media_process import ProcessLimitError, ProcessLimits, run_bounded

OUTPUT_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ProcessVideoAnalyzer:
    executable: Path
    model_path: Path
    model_version: str
    data_schema_version: str
    max_rss_bytes: int

    async def analyze(self, prepared: PreparedVideo) -> AnalysisOutcome:
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
                    # The enclosing processor enforces the accepted job's timeout. A deploy must not
                    # silently replace that timeout with today's settings. Cancellation reaps the child.
                    timeout_seconds=86_400,
                    max_stdout_bytes=OUTPUT_BYTES,
                    max_line_bytes=OUTPUT_BYTES,
                    max_rss_bytes=self.max_rss_bytes,
                ),
            )
        except ProcessLimitError as exc:
            code: PublicErrorCode = "PROCESSING_TIMEOUT" if exc.kind == "timeout" else "INTERNAL_ERROR"
            raise VideoStageError(code) from exc
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
