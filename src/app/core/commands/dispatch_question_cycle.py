from __future__ import annotations

import json
import logging
from typing import Any

from app.core.common.interview_qa.dto import CallbackErrorDetail, CallbackFailure
from app.core.common.interview_qa.errors import PipelineError
from app.core.common.interview_qa.ports.webhook_client import WebhookClient
from app.core.common.question_cycle.dto import (
    QuestionCycleCallbackSuccess,
    QuestionCycleRequest,
    QuestionCycleResult,
)
from app.core.common.question_cycle.generate import QuestionCycleGenerate

logger = logging.getLogger(__name__)


class DispatchQuestionCycle:
    def __init__(self, webhook: WebhookClient, question_cycle_generate: QuestionCycleGenerate) -> None:
        # 콜백 전송 어댑터. 구현체는 DI 가 결정.
        self._webhook = webhook
        # 사이클 생성 — LLM 1회 호출 + 검증 실패 시 재시도. 끝내 실패하면 PipelineError.
        self._generate = question_cycle_generate

    async def execute(self, job_id: str, job_request: QuestionCycleRequest) -> None:
        logger.info(
            "question_cycle.dispatch.start",
            extra={
                "job_id": job_id,
                "mode": job_request.mode,
                "exclude_count": len(job_request.exclude_questions),
            },
        )

        try:
            payload = await self._build_payload(job_id, job_request)
        except Exception:
            # 알 수 없는 내부 오류 — 500 으로 콜백 전송.
            # 백그라운드 작업은 예외를 응답으로 알릴 수 없어서, 여기서 반드시 삼켜야 한다.
            logger.exception("question_cycle.dispatch.unexpected_error", extra={"job_id": job_id})
            payload = _failure_payload(job_id, 500, "내부 오류로 작업을 완료하지 못했습니다.")

        logger.debug("question_cycle.dispatch.payload payload=%s", json.dumps(payload, ensure_ascii=False))
        await self._webhook.send(job_request.callback_url, payload)
        logger.info("question_cycle.dispatch.done", extra={"job_id": job_id})

    async def _build_payload(self, job_id: str, job_request: QuestionCycleRequest) -> dict[str, Any]:
        try:
            questions = await self._generate.execute(job_request)
        except PipelineError as exc:
            return _failure_payload(job_id, exc.status_code, exc.message)

        return QuestionCycleCallbackSuccess(
            job_id=job_id,
            result=QuestionCycleResult(mode=job_request.mode, questions=questions),
        ).model_dump(by_alias=True)


def _failure_payload(job_id: str, status_code: int, message: str) -> dict[str, Any]:
    return CallbackFailure(
        job_id=job_id,
        error=CallbackErrorDetail(status_code=status_code, message=message),
    ).model_dump(by_alias=True)
