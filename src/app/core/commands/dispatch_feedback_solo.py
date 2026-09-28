from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError

from app.core.common.feedback.dto import FeedbackCallbackFailure, FeedbackErrorDetail
from app.core.common.feedback.scoring import SessionScores, summarize_session
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
        logger.info(
            "feedback_solo.dispatch.start",
            extra={"job_id": job_id, "session_id": job_request.session_id},
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
        await self._webhook.send(job_request.callback_url, payload)
        logger.info("feedback_solo.dispatch.done", extra={"job_id": job_id})

    async def _build_payload(self, job_id: str, job_request: FeedbackSoloRequest) -> dict[str, Any]:
        try:
            assembled = self._assembly.execute(job_request)
            raw_result = await self._grading.execute(
                assembled,
                job_request.persona_type,
                job_request.persona_tone,
            )
            scores = _session_scores(assembled, raw_result)
            result = self._build_result(assembled, raw_result, scores)
            question_levels = {
                question_id: entry["axis_levels"].as_dict() for question_id, entry in raw_result["feedbacks"].items()
            }
            logger.info(
                "feedback_solo.dispatch.graded scoring=%s",
                json.dumps(
                    {
                        "job_id": job_id,
                        **scores.log_extra(),
                        "question_levels": question_levels,
                    },
                    ensure_ascii=False,
                ),
                extra={
                    "job_id": job_id,
                    "total_score": result.overall.total_score,
                    "answered_count": result.overall.answered_count,
                    "question_count": result.overall.question_count,
                    # 산출 근거는 API 계약에 반영하기 전까지 로그로만 남긴다.
                    **scores.log_extra(),
                    "question_levels": question_levels,
                },
            )
            return FeedbackCallbackSuccess(
                job_id=job_id,
                session_id=job_request.session_id,
                result=result,
            ).model_dump(by_alias=True)
        except PipelineError as exc:
            return _failure_payload(job_id, job_request.session_id, exc.status_code, exc.message)

    def _build_result(
        self,
        assembled: AssembledSession,
        raw_result: dict[str, Any],
        scores: SessionScores,
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

        graded_overall: dict[str, Any] = raw_result["overall"]
        # 3지표는 LLM 이 아니라 서버가 축 등급으로 계산한 값이다(scoring.py).
        overall: dict[str, Any] = {
            "total_score": scores.total_score,
            "intent_alignment_score": scores.intent_alignment_score,
            "reliability_score": scores.reliability_score,
            # 누락된 필수 텍스트 필드는 기본값으로 숨기지 않고 아래 모델 검증에 맡긴다.
            **{key: graded_overall[key] for key in ("summary", "strengths", "improvements") if key in graded_overall},
        }
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


def _session_scores(assembled: AssembledSession, raw_result: dict[str, Any]) -> SessionScores:
    graded: dict[str, Any] = raw_result["feedbacks"]
    levels = [graded[target.question_id]["axis_levels"] for target in assembled.targets]
    scores = summarize_session(levels, raw_result["consistency"])
    if scores is None:
        # 조립 단계에서 전 문항 미답변은 이미 422 로 걸러진다. 여기 오면 내부 버그다.
        raise PipelineError(500, "피드백 결과가 형식을 충족하지 못했습니다.")
    return scores


def _failure_payload(job_id: str, session_id: str, status_code: int, message: str) -> dict[str, Any]:
    return FeedbackCallbackFailure(
        job_id=job_id,
        session_id=session_id,
        error=FeedbackErrorDetail(status_code=status_code, message=message),
    ).model_dump(by_alias=True)
