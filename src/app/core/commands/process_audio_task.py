from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.core.common.audio.dto import AudioError, AudioPolicy, AudioRequest, PreparedAnswerAudio, Region, wire_payload
from app.core.common.audio.fluency import analyze_fluency
from app.core.common.audio.ports import AudioRepository, AudioStageBackend
from app.core.common.audio.preprocessing import DEPENDENCIES, assemble_prepared, complement
from app.core.common.audio.timing import analyze_timing
from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicTextClient, AnthropicTextClientError
from app.core.common.interview_qa.ports.webhook_client import WebhookClient

logger = logging.getLogger(__name__)


class AudioLeaseLostError(Exception):
    """Stop this worker: a stale executor must never overwrite a new owner's result."""


class ProcessAudioTask:
    def __init__(
        self,
        repository: AudioRepository,
        backend: AudioStageBackend,
        fluency_client: AnthropicTextClient | None,
        webhook: WebhookClient,
        capacities: dict[str, int],
        lease_seconds: int = 180,
    ) -> None:
        self.repository = repository
        self.backend = backend
        self.fluency_client = fluency_client
        self.webhook = webhook
        self.capacities = capacities
        self.lease_seconds = lease_seconds

    async def run_once(self) -> bool:
        task = await asyncio.to_thread(self.repository.claim, self.capacities, self.lease_seconds)
        if task is None:
            return False
        heartbeat = asyncio.create_task(self._heartbeat(task))
        execution = asyncio.create_task(self._execute(task))
        try:
            done, _ = await asyncio.wait((heartbeat, execution), return_when=asyncio.FIRST_COMPLETED)
            if heartbeat in done:
                await heartbeat
                raise AudioLeaseLostError
            result = await execution
            if not await asyncio.to_thread(self.repository.finish, task, result):
                raise AudioLeaseLostError
        except AudioLeaseLostError:
            raise
        except AudioError as exc:
            await self._fail(task, exc.code, retryable=exc.retryable)
        except (TimeoutError, AnthropicTextClientError):
            await self._fail(task, "external_service_unavailable", retryable=True)
        except Exception:
            # Do not log transcript, paths, callback URLs, or provider error bodies.
            logger.error("audio.stage_failed job=%s stage=%s", task["job_id"], task["stage"])
            await self._fail(task, "internal_stage_error", retryable=False)
        finally:
            heartbeat.cancel()
            execution.cancel()
            await asyncio.gather(heartbeat, execution, return_exceptions=True)
        return True

    async def _fail(self, task: dict[str, Any], code: str, *, retryable: bool) -> None:
        if not await asyncio.to_thread(self.repository.fail, task, code, retryable=retryable):
            raise AudioLeaseLostError

    async def _heartbeat(self, task: dict[str, Any]) -> None:
        while True:
            await asyncio.sleep(self.lease_seconds / 3)
            if not await asyncio.to_thread(
                self.repository.heartbeat,
                task["id"],
                task["token"],
                self.lease_seconds,
            ):
                raise AudioLeaseLostError

    async def _execute(self, task: dict[str, Any]) -> dict[str, Any]:  # noqa: PLR0911 — explicit stage dispatch
        context = await asyncio.to_thread(self.repository.context, task)
        request = AudioRequest.model_validate(context["request"])
        policy = AudioPolicy.model_validate(context["policy"])
        stage = task["stage"]
        if stage == "aggregate":
            return _aggregate(task["job_id"], request, context["tasks"])
        if stage == "callback":
            return await self._callback(request, context["tasks"])
        answer = request.answers[task["answer_index"]]
        stages = {item["stage"]: item for item in context["tasks"] if item["answer_index"] == task["answer_index"]}
        if stage == "prepare":
            return assemble_prepared(answer, policy, stages).model_dump()
        if any(stages[dependency]["status"] != "succeeded" for dependency in DEPENDENCIES[stage]):
            return {"stage_blocked": True, "reason": "required_input_failed"}
        if stage in {"timing", "fluency"}:
            prepared = PreparedAnswerAudio.model_validate(stages["prepare"]["result"])
            if stage == "timing":
                return analyze_timing(prepared, policy)
            if prepared.transcript is None or not prepared.transcript.text.strip():
                return {"status": "unavailable", "reason": "transcript_unavailable_or_empty"}
            if self.fluency_client is None:
                raise AudioError("fluency_client_not_configured")
            async with asyncio.timeout(180):
                return await analyze_fluency(prepared, answer, policy, self.fluency_client)
        inputs = {name: item["result"] for name, item in stages.items() if item["status"] == "succeeded"}
        result = await self.backend.execute(stage, answer, policy, inputs)
        _validate_stage_result(stage, result, inputs)
        return result

    async def _callback(self, request: AudioRequest, tasks: list[dict[str, Any]]) -> dict[str, Any]:
        if request.callback_url is None:
            return {"delivery": "not_requested"}
        aggregate = next(item for item in tasks if item["stage"] == "aggregate")
        if aggregate["status"] != "succeeded":
            return {"stage_blocked": True, "reason": "aggregate_failed"}
        if not await self.webhook.send(request.callback_url, wire_payload(aggregate["result"])):
            raise AudioError("callback_delivery_failed", retryable=True)
        return {"delivery": "sent"}


def _validate_stage_result(stage: str, result: dict[str, Any], inputs: dict[str, dict[str, Any]]) -> None:
    if stage == "vad":
        try:
            complement([Region.model_validate(item) for item in result["speech"]], inputs["normalize"]["duration_ms"])
        except ValueError as exc:
            raise AudioError("invalid_vad_timeline") from exc


def _aggregate(job_id: str, request: AudioRequest, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    answers = []
    for index, answer in enumerate(request.answers):
        stages = {task["stage"]: task for task in tasks if task["answer_index"] == index}

        def line(name: str, entries: dict[str, Any] = stages) -> dict[str, Any]:
            task = entries[name]
            if task["status"] == "succeeded":
                return dict(task["result"])
            return {"status": "unavailable", "reason": task["error"] or "required_input_failed"}

        prepared = stages["prepare"]["result"] or {}
        answers.append(
            {
                "answerId": answer.answer_id,
                "questionId": answer.question_id,
                "preprocessingStatus": prepared.get("status", "unusable"),
                "durationMs": prepared.get("duration_ms"),
                "timing": line("timing"),
                "fluency": line("fluency"),
            }
        )
    line_statuses = [item[name]["status"] for item in answers for name in ("timing", "fluency")]
    status = "ready" if all(value == "ready" for value in line_statuses) else "partial"
    if all(value == "unavailable" for value in line_statuses):
        status = "unusable"
    return {"jobId": job_id, "sessionId": request.session_id, "status": status, "answers": answers}
