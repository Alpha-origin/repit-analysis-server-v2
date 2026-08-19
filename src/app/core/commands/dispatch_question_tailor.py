from __future__ import annotations

import json
import logging
from typing import Any

from app.core.common.interview_qa.errors import PipelineError
from app.core.common.interview_qa.ports.webhook_client import WebhookClient
from app.core.common.question_tailor.dto import (
    QuestionTailorCallbackFailure,
    QuestionTailorCallbackSuccess,
    QuestionTailorErrorDetail,
    QuestionTailorRequest,
    QuestionTailorResult,
    TailoredQuestion,
)
from app.core.common.question_tailor.rewrite import QuestionRewrite

logger = logging.getLogger(__name__)


class DispatchQuestionTailor:
    def __init__(self, webhook: WebhookClient, question_rewrite: QuestionRewrite) -> None:
        # 콜백 전송 어댑터. 구현체는 DI 가 결정.
        self._webhook = webhook
        # 재작성 — LLM 1회 호출. 실패하면 None 을 돌려주고, 여기서 원질문으로 폴백한다.
        self._rewrite = question_rewrite

    async def execute(self, job_id: str, job_request: QuestionTailorRequest) -> None:
        logger.info(
            "question_tailor.dispatch.start",
            extra={"job_id": job_id, "interview_id": job_request.interview_id},
        )

        try:
            payload = await self._build_payload(job_id, job_request)
        except Exception:
            # 알 수 없는 내부 오류 — 500 으로 콜백 전송.
            # 백그라운드 작업은 예외를 응답으로 알릴 수 없어서, 여기서 반드시 삼켜야 한다.
            logger.exception("question_tailor.dispatch.unexpected_error", extra={"job_id": job_id})
            payload = _failure_payload(
                job_id,
                job_request.interview_id,
                500,
                "내부 오류로 작업을 완료하지 못했습니다.",
            )

        logger.debug("question_tailor.dispatch.payload payload=%s", json.dumps(payload, ensure_ascii=False))
        await self._webhook.send(job_request.callback_url, payload)
        logger.info("question_tailor.dispatch.done", extra={"job_id": job_id})

    async def _build_payload(self, job_id: str, job_request: QuestionTailorRequest) -> dict[str, Any]:
        try:
            rewritten = await self._rewrite.execute(job_request)
        except PipelineError as exc:
            return _failure_payload(job_id, job_request.interview_id, exc.status_code, exc.message)

        tailored = rewritten is not None
        if rewritten is None:
            # 재작성 실패 — 원질문은 이미 유효한 산출물이므로 그대로 돌려주고 플래그로 알린다.
            logger.warning("question_tailor.dispatch.fallback_to_original", extra={"job_id": job_id})
            rewritten = tuple(
                TailoredQuestion(id=question.id, question=question.question) for question in job_request.questions
            )

        logger.info(
            "question_tailor.dispatch.rewritten",
            extra={"job_id": job_id, "tailored": tailored, "question_count": len(rewritten)},
        )
        return QuestionTailorCallbackSuccess(
            job_id=job_id,
            interview_id=job_request.interview_id,
            result=QuestionTailorResult(tailored=tailored, questions=list(rewritten)),
        ).model_dump(by_alias=True)


def _failure_payload(job_id: str, interview_id: str, status_code: int, message: str) -> dict[str, Any]:
    return QuestionTailorCallbackFailure(
        job_id=job_id,
        interview_id=interview_id,
        error=QuestionTailorErrorDetail(status_code=status_code, message=message),
    ).model_dump(by_alias=True)
