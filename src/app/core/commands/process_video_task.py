from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.core.common.video.dto import AnalysisOutcome, PreparedVideo, TerminalCallback
from app.core.common.video.errors import PublicErrorCode, VideoStageError
from app.core.common.video.ports import (
    DEPENDENCIES,
    RepositoryUnavailableError,
    Stage,
    StageContext,
    StageOutput,
    StageTask,
    VideoAnalyzer,
    VideoMediaBackend,
    VideoRepository,
    VideoResourceUnavailableError,
)
from app.core.common.video.terminal import build_terminal_callback, failed_callback

logger = logging.getLogger(__name__)


class VideoLeaseLostError(Exception):
    """This worker no longer owns the stage; it must stop without writing anything."""


@dataclass(frozen=True)
class _Blocked:
    cause: PublicErrorCode


class ProcessVideoTask:
    """Runs one claimed stage of the video DAG ``source -> inspect -> validate -> analyze -> finalize``.

    Successful stages are never re-executed. Upstream failures block downstream work but finalize
    always runs, so every job converges to one stored terminal callback.
    """

    def __init__(
        self,
        repository: VideoRepository,
        media: VideoMediaBackend,
        analyzer: VideoAnalyzer,
        capacities: dict[str, int],
        lease_seconds: int = 180,
    ) -> None:
        self.repository = repository
        self.media = media
        self.analyzer = analyzer
        self.capacities = capacities
        self.lease_seconds = lease_seconds

    async def run_once(self) -> bool:
        task = await asyncio.to_thread(self.repository.claim_stage, self.capacities, self.lease_seconds)
        if task is None:
            return False
        heartbeat = asyncio.create_task(self._heartbeat(task))
        execution = asyncio.create_task(self._execute(task))
        try:
            done, _ = await asyncio.wait((heartbeat, execution), return_when=asyncio.FIRST_COMPLETED)
            if heartbeat in done:
                heartbeat.result()
                raise VideoLeaseLostError
            await self._persist(task, execution.result())
        except VideoLeaseLostError:
            logger.warning("video.stage.lease_lost job=%s stage=%s", task.job_id, task.stage)
        except VideoResourceUnavailableError:
            try:
                await asyncio.to_thread(self.repository.defer_stage, task)
                logger.info("video.stage.disk_wait job=%s stage=%s", task.job_id, task.stage)
            except RepositoryUnavailableError:
                logger.error("video.stage.defer_not_stored job=%s stage=%s", task.job_id, task.stage)
        except VideoStageError as exc:
            await self._fail(task, exc.code, stage_retry=exc.stage_retry)
        except RepositoryUnavailableError:
            # Never claim persistence that did not happen; the lease expires and the stage is reclaimed.
            logger.error("video.stage.storage_error job=%s stage=%s", task.job_id, task.stage)
        except Exception:
            # Do not log exception text: it may contain signed URLs or file paths.
            logger.error("video.stage.internal_error job=%s stage=%s", task.job_id, task.stage)
            await self._fail(task, "INTERNAL_ERROR", stage_retry=False)
        finally:
            # Cancelling execution kills and reaps any media child and removes its temporary files.
            heartbeat.cancel()
            execution.cancel()
            await asyncio.gather(heartbeat, execution, return_exceptions=True)
            try:
                await asyncio.to_thread(self.repository.release_empty_artifacts, task)
            except (RepositoryUnavailableError, OSError):
                logger.error("video.stage.reservation_cleanup_failed job=%s", task.job_id)
        return True

    async def _persist(self, task: StageTask, outcome: StageOutput | _Blocked | TerminalCallback) -> None:
        if isinstance(outcome, TerminalCallback):
            stored = await asyncio.to_thread(self.repository.terminalize, task, outcome)
        elif isinstance(outcome, _Blocked):
            stored = await asyncio.to_thread(self.repository.block_stage, task, outcome.cause)
        else:
            stored = await asyncio.to_thread(self.repository.finish_stage, task, outcome)
        if not stored:
            raise VideoLeaseLostError

    async def _fail(self, task: StageTask, code: PublicErrorCode, *, stage_retry: bool) -> None:
        try:
            stored = await asyncio.to_thread(self.repository.fail_stage, task, code, stage_retry=stage_retry)
        except Exception:
            logger.error("video.stage.fail_not_stored job=%s stage=%s", task.job_id, task.stage)
            return
        if not stored:
            logger.warning("video.stage.lease_lost job=%s stage=%s", task.job_id, task.stage)
        else:
            logger.info("video.stage.failed job=%s stage=%s code=%s", task.job_id, task.stage, code)

    async def _heartbeat(self, task: StageTask) -> None:
        while True:
            await asyncio.sleep(self.lease_seconds / 3)
            if not await asyncio.to_thread(self.repository.heartbeat, task, self.lease_seconds):
                raise VideoLeaseLostError

    async def _execute(self, task: StageTask) -> StageOutput | _Blocked | TerminalCallback:
        context = await asyncio.to_thread(self.repository.stage_context, task)
        if task.stage == "finalize":
            return self._terminal(task, context)
        for dependency in DEPENDENCIES[task.stage]:
            record = context.dependencies[dependency]
            if record.status != "succeeded":
                # Propagate the upstream public cause; finalize reports it once.
                return _Blocked(record.error_code or "INTERNAL_ERROR")
        limits = context.policy.limits
        results = {stage: record.result or {} for stage, record in context.dependencies.items()}
        if task.stage == "source":
            reservation = await asyncio.to_thread(self.repository.reserve_artifact, task, "source")
            if reservation is None:
                raise VideoLeaseLostError
            data = await self.media.download(context.request, limits, reservation)
            return StageOutput(data=data, staged_artifacts=(reservation,))
        if task.stage == "inspect":
            return StageOutput(data=await self.media.inspect(results["source"]["path"], limits))
        if task.stage == "validate":
            return StageOutput(data=await self.media.validate(results["source"]["path"], results["inspect"], limits))
        return StageOutput(data=await self._analyze(context, results))

    async def _analyze(self, context: StageContext, results: dict[Stage, dict[str, Any]]) -> dict[str, Any]:
        source, inspected, validated = results["source"], results["inspect"], results["validate"]
        prepared = PreparedVideo.model_validate(
            {
                "video_id": context.request.video.video_id,
                "path": source["path"],
                "sha256": source["sha256"],
                "bytes": source["bytes"],
                "container": inspected["container"],
                "codec": inspected["codec"],
                "display_long_edge": inspected["displayLongEdge"],
                "display_short_edge": inspected["displayShortEdge"],
                "rotation_degrees": inspected["rotationDegrees"],
                "frame_count": validated["frameCount"],
                "duration_ms": validated["durationMs"],
                "average_fps": validated["averageFps"],
            }
        )
        try:
            async with asyncio.timeout(context.policy.limits.analyze_timeout_seconds):
                outcome = await self.analyzer.analyze(prepared)
        except TimeoutError as exc:
            raise VideoStageError("PROCESSING_TIMEOUT", stage_retry=True) from exc
        try:
            # Re-validate: an analyzer can never smuggle an inconsistent status/data/error combination.
            checked = AnalysisOutcome.model_validate(outcome.model_dump())
        except ValidationError as exc:
            raise VideoStageError("INTERNAL_ERROR") from exc
        return checked.wire()

    @staticmethod
    def _terminal(task: StageTask, context: StageContext) -> TerminalCallback:
        try:
            return build_terminal_callback(task.job_id, context.request, context.dependencies)
        except Exception:
            # Document generation failed: the job still converges with a minimal failed callback.
            logger.error("video.finalize.generation_failed job=%s", task.job_id)
            return failed_callback(task.job_id, context.request)
