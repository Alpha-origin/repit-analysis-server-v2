from __future__ import annotations

import json
import logging
from time import perf_counter
from typing import Any

from pydantic import ValidationError

from app.core.common.feedback.dto import FeedbackCallbackFailure, FeedbackErrorDetail
from app.core.common.feedback.solo.answer_assembly import AnswerAssembly
from app.core.common.feedback.solo.answer_grading import AnswerGrading
from app.core.common.feedback.solo.dto import (
    AssembledSession,
    FeedbackCallbackSuccess,
    FeedbackSoloRequest,
    InterviewFeedbackResult,
)
from app.core.common.feedback.solo.word_frequency import extract_frequent_words
from app.core.common.interview_qa.errors import PipelineError
from app.core.common.interview_qa.ports.webhook_client import WebhookClient

logger = logging.getLogger(__name__)


class DispatchFeedbackSolo:
    def __init__(
        self,
        webhook: WebhookClient,
        answer_assembly: AnswerAssembly,
        answer_grading: AnswerGrading,
        frequent_word_top_n: int,
        frequent_word_min_count: int,
    ) -> None:
        # 콜백 전송 어댑터. 구현체는 DI 가 결정.
        self._webhook = webhook
        # 1단계 — 질문 x 답변 조인 + 부모 맥락 부착 + 미답변 분리.
        self._assembly = answer_assembly
        # 2단계 — 전 문항 1회 LLM 호출로 채점.
        self._grading = answer_grading
        self._top_n = frequent_word_top_n
        self._min_count = frequent_word_min_count

    async def execute(self, job_id: str, job_request: FeedbackSoloRequest) -> None:
        pipeline_started_at = perf_counter()
        logger.info(
            "feedback_solo.dispatch.start",
            extra={
                "job_id": job_id,
                "session_id": job_request.session_id,
                "question_count": len(job_request.questions),
                "answer_count": len(job_request.answers),
            },
        )

        try:
            payload = await self._build_payload(job_id, job_request)
        except Exception:
            # 알 수 없는 내부 오류 — 500 으로 콜백 전송.
            # 백그라운드 작업은 예외를 응답으로 알릴 수 없어서, 여기서 반드시 삼켜야 한다.
            logger.exception("feedback_solo.dispatch.unexpected_error", extra={"job_id": job_id})
            payload = _failure_payload(
                job_id,
                job_request.session_id,
                500,
                "내부 오류로 작업을 완료하지 못했습니다.",
            )

        logger.debug("feedback_solo.dispatch.payload payload=%s", json.dumps(payload, ensure_ascii=False))
        callback_started_at = perf_counter()
        logger.info(
            "feedback_solo.dispatch.callback.start",
            extra={"job_id": job_id, "status": payload.get("status")},
        )
        try:
            callback_sent = await self._webhook.send(job_request.callback_url, payload)
        except Exception:
            logger.exception(
                "feedback_solo.dispatch.callback.failed",
                extra={
                    "job_id": job_id,
                    "duration_ms": _elapsed_ms(callback_started_at),
                    "pipeline_duration_ms": _elapsed_ms(pipeline_started_at),
                },
            )
            raise

        logger.info(
            "feedback_solo.dispatch.callback.done",
            extra={
                "job_id": job_id,
                "delivered": callback_sent,
                "duration_ms": _elapsed_ms(callback_started_at),
            },
        )
        logger.info(
            "feedback_solo.dispatch.done",
            extra={
                "job_id": job_id,
                "status": payload.get("status"),
                "callback_delivered": callback_sent,
                "duration_ms": _elapsed_ms(pipeline_started_at),
            },
        )

    async def _build_payload(self, job_id: str, job_request: FeedbackSoloRequest) -> dict[str, Any]:
        stage = "answer_assembly"
        stage_started_at = perf_counter()
        try:
            logger.info(
                "feedback_solo.dispatch.stage.start",
                extra={"job_id": job_id, "stage": stage},
            )
            assembled = self._assembly.execute(job_request)

            logger.info(
                "feedback_solo.dispatch.stage.done",
                extra={
                    "job_id": job_id,
                    "stage": stage,
                    "duration_ms": _elapsed_ms(stage_started_at),
                    "answered_count": len(assembled.targets),
                    "unanswered_count": len(assembled.unanswered_question_ids),
                },
            )

            stage = "llm_grading"
            stage_started_at = perf_counter()
            logger.info(
                "feedback_solo.dispatch.stage.start",
                extra={"job_id": job_id, "stage": stage, "target_count": len(assembled.targets)},
            )
            raw_result = await self._grading.execute(assembled, job_request.persona_type)

            logger.info(
                "feedback_solo.dispatch.stage.done",
                extra={
                    "job_id": job_id,
                    "stage": stage,
                    "duration_ms": _elapsed_ms(stage_started_at),
                },
            )

            stage = "result_building"
            stage_started_at = perf_counter()
            logger.info(
                "feedback_solo.dispatch.stage.start",
                extra={"job_id": job_id, "stage": stage},
            )
            result = self._build_result(assembled, raw_result)
            logger.info(
                "feedback_solo.dispatch.stage.done",
                extra={
                    "job_id": job_id,
                    "stage": stage,
                    "duration_ms": _elapsed_ms(stage_started_at),
                    "feedback_count": len(result.feedbacks),
                },
            )
            logger.info(
                "feedback_solo.dispatch.graded",
                extra={
                    "job_id": job_id,
                    "total_score": result.overall.total_score,
                    "answered_count": result.overall.answered_count,
                    "question_count": result.overall.question_count,
                },
            )
            logger.info(
                "feedback_solo.dispatch.final_feedback",
                extra={"job_id": job_id, "feedback": _feedback_for_log(result)},
            )
            return FeedbackCallbackSuccess(
                job_id=job_id,
                session_id=job_request.session_id,
                result=result,
            ).model_dump(by_alias=True)
        except PipelineError as exc:
            logger.warning(
                "feedback_solo.dispatch.stage.failed",
                extra={
                    "job_id": job_id,
                    "stage": stage,
                    "duration_ms": _elapsed_ms(stage_started_at),
                    "status_code": exc.status_code,
                    "error": exc.message,
                },
            )
            return _failure_payload(job_id, job_request.session_id, exc.status_code, exc.message)
        except Exception:
            logger.exception(
                "feedback_solo.dispatch.stage.failed",
                extra={
                    "job_id": job_id,
                    "stage": stage,
                    "duration_ms": _elapsed_ms(stage_started_at),
                },
            )
            raise

    def _build_result(
        self,
        assembled: AssembledSession,
        raw_result: dict[str, Any],
    ) -> InterviewFeedbackResult:
        graded: dict[str, Any] = raw_result["feedbacks"]

        # 문항 순서는 LLM 응답 순서가 아니라 실제 면접 진행 순서를 따른다.
        # 질문 본문/의도/사용자 답변은 요청 body 값을 그대로 되돌려준다(LLM 생성분이 아니다).
        feedbacks = [
            {
                "question_id": target.question_id,
                "question_content": target.content,
                "intention": target.intention,
                "user_answer": target.answer,
                "model_answer": graded[target.question_id].get("model_answer", ""),
                "strengths": graded[target.question_id].get("strengths", []),
                "improvements": graded[target.question_id].get("improvements", []),
                "comment": graded[target.question_id].get("comment", ""),
            }
            for target in assembled.targets
        ]

        overall: dict[str, Any] = dict(raw_result["overall"])
        overall["frequent_words"] = [
            {"word": word, "count": count}
            for word, count in extract_frequent_words(
                (target.answer for target in assembled.targets),
                top_n=self._top_n,
                min_count=self._min_count,
            )
        ]
        overall["answered_count"] = len(assembled.targets)
        overall["question_count"] = assembled.question_count

        try:
            # LLM 응답에 대한 마지막 관문. 점수 범위·타입이 어긋나면 여기서 걸린다.
            return InterviewFeedbackResult.model_validate({"overall": overall, "feedbacks": feedbacks})
        except ValidationError as exc:
            logger.warning("feedback_solo.dispatch.result_validation_failed", extra={"error": str(exc)})
            raise PipelineError(500, "피드백 결과가 형식을 충족하지 못했습니다.") from exc


def _failure_payload(job_id: str, session_id: str, status_code: int, message: str) -> dict[str, Any]:
    return FeedbackCallbackFailure(
        job_id=job_id,
        session_id=session_id,
        error=FeedbackErrorDetail(status_code=status_code, message=message),
    ).model_dump(by_alias=True)


def _feedback_for_log(result: InterviewFeedbackResult) -> dict[str, Any]:
    """사용자 원문을 제외하고 실제 생성된 피드백만 운영 로그로 직렬화한다."""
    return {
        "overall": result.overall.model_dump(by_alias=True),
        "feedbacks": [
            {
                "questionId": feedback.question_id,
                "modelAnswer": feedback.model_answer,
                "strengths": feedback.strengths,
                "improvements": feedback.improvements,
                "comment": feedback.comment,
            }
            for feedback in result.feedbacks
        ],
    }


def _elapsed_ms(started_at: float) -> float:
    return round((perf_counter() - started_at) * 1000, 2)
