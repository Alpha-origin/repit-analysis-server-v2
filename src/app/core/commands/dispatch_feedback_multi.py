from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError

from app.core.common.feedback.dto import FeedbackCallbackFailure, FeedbackErrorDetail
from app.core.common.feedback.multi.answer_grading import MultiAnswerGrading
from app.core.common.feedback.multi.dto import (
    FeedbackMultiRequest,
    FeedbackPersona,
    MultiFeedbackCallbackSuccess,
    MultiInterviewFeedbackResult,
)
from app.core.common.feedback.scoring import (
    AxisLevels,
    SessionScores,
    compute_breakdown,
    summarize_session,
    to_breakdown_payload,
)
from app.core.common.feedback.solo.answer_assembly import AnswerAssembly
from app.core.common.feedback.solo.dto import AssembledSession
from app.core.common.feedback.solo.word_frequency import extract_frequent_words
from app.core.common.interview_qa.errors import PipelineError
from app.core.common.interview_qa.ports.webhook_client import WebhookClient

logger = logging.getLogger(__name__)

# 정확성(기술 개념 오류) 축은 기술 면접관의 문항에만 적용한다.
# 비개발 직책 질문에 기술 정확성을 매기면 직책을 나눈 의미가 사라진다.
_TECH_ROLE = "TECH"


class DispatchFeedbackMulti:
    def __init__(
        self,
        webhook: WebhookClient,
        answer_assembly: AnswerAssembly,
        answer_grading: MultiAnswerGrading,
        frequent_word_top_n: int,
        frequent_word_min_count: int,
    ) -> None:
        # 콜백 전송 어댑터. 구현체는 DI 가 결정.
        self._webhook = webhook
        # 1단계 — 질문 x 답변 조인 + 부모 맥락 부착 + 미답변 분리.
        # 단일 세션 구조라 체인 복원이 1:1 과 같아서 solo 구현을 그대로 쓴다.
        self._assembly = answer_assembly
        # 2단계 — 전 문항 + 면접관별 평가를 1회 LLM 호출로 채점.
        self._grading = answer_grading
        self._top_n = frequent_word_top_n
        self._min_count = frequent_word_min_count

    async def execute(self, job_id: str, job_request: FeedbackMultiRequest) -> None:
        logger.info(
            "feedback_multi.dispatch.start",
            extra={
                "job_id": job_id,
                "session_id": job_request.session_id,
                "persona_count": len(job_request.personas),
            },
        )

        try:
            payload = await self._build_payload(job_id, job_request)
        except Exception:
            # 알 수 없는 내부 오류 — 500 으로 콜백 전송.
            # 백그라운드 작업은 예외를 응답으로 알릴 수 없어서, 여기서 반드시 삼켜야 한다.
            logger.exception("feedback_multi.dispatch.unexpected_error", extra={"job_id": job_id})
            payload = _failure_payload(
                job_id,
                job_request.session_id,
                500,
                "내부 오류로 작업을 완료하지 못했습니다.",
            )

        logger.debug("feedback_multi.dispatch.payload payload=%s", json.dumps(payload, ensure_ascii=False))
        await self._webhook.send(job_request.callback_url, payload)
        logger.info("feedback_multi.dispatch.done", extra={"job_id": job_id})

    async def _build_payload(self, job_id: str, job_request: FeedbackMultiRequest) -> dict[str, Any]:
        try:
            assembled = self._assembly.assemble(
                job_request.questions,
                job_request.answers,
                session_id=job_request.session_id,
            )
            persona_by_question = _persona_by_question(job_request)
            raw_result = await self._grading.execute(assembled, job_request.personas, persona_by_question)
            levels = _question_levels(assembled, persona_by_question, raw_result)
            scores = _session_scores(levels, raw_result)
            result = self._build_result(job_request, assembled, persona_by_question, raw_result, levels, scores)
            question_levels = {question_id: level.as_dict() for question_id, level in levels.items()}
            logger.info(
                "feedback_multi.dispatch.graded scoring=%s",
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
                    "persona_scores": [persona.score for persona in result.personas],
                    "answered_count": result.overall.answered_count,
                    "question_count": result.overall.question_count,
                    # 축별 점수는 콜백 score_breakdown 에도 실리지만, 문항별 등급은 로그에만 남는다.
                    **scores.log_extra(),
                    "question_levels": question_levels,
                },
            )
            return MultiFeedbackCallbackSuccess(
                job_id=job_id,
                session_id=job_request.session_id,
                result=result,
            ).model_dump(by_alias=True)
        except PipelineError as exc:
            return _failure_payload(job_id, job_request.session_id, exc.status_code, exc.message)

    def _build_result(
        self,
        job_request: FeedbackMultiRequest,
        assembled: AssembledSession,
        persona_by_question: dict[str, FeedbackPersona],
        raw_result: dict[str, Any],
        levels: dict[str, AxisLevels],
        scores: SessionScores,
    ) -> MultiInterviewFeedbackResult:
        graded: dict[str, Any] = raw_result["feedbacks"]
        graded_personas: dict[str, Any] = raw_result["personas"]

        # 문항 순서는 LLM 응답 순서가 아니라 실제 면접 진행 순서를 따른다.
        # 질문 본문/의도/사용자 답변/면접관은 요청 body 값을 그대로 되돌려준다(LLM 생성분이 아니다).
        feedbacks = [
            {
                "question_id": target.question_id,
                "persona_id": _persona_id_of(persona_by_question, target.question_id),
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

        # 면접관 순서도 요청 순서를 따른다. role 은 LLM 이 아니라 요청 값을 쓴다.
        personas = [
            {
                "persona_id": persona.persona_id,
                "role": persona.role,
                **_persona_score_fields(persona, assembled, persona_by_question, levels),
                "comment": graded_personas[persona.persona_id].get("comment", ""),
                "strengths": graded_personas[persona.persona_id].get("strengths", []),
                "improvements": graded_personas[persona.persona_id].get("improvements", []),
            }
            for persona in job_request.personas
        ]

        graded_overall: dict[str, Any] = raw_result["overall"]
        # 3지표는 LLM 이 아니라 서버가 축 등급으로 계산한 값이다(scoring.py).
        overall: dict[str, Any] = {
            "total_score": scores.total_score,
            "intent_alignment_score": scores.intent_alignment_score,
            "reliability_score": scores.reliability_score,
            # 사용자에게 종합 점수의 산출 과정(축별 점수 x 가중치)을 보여주기 위한 근거.
            "score_breakdown": scores.breakdown_payload(),
            # 누락된 필수 텍스트 필드는 기본값으로 숨기지 않고 아래 모델 검증에 맡긴다.
            **{key: graded_overall[key] for key in ("summary", "strengths", "improvements") if key in graded_overall},
        }
        # 자주 사용한 단어는 면접관과 무관하게 인터뷰 전체를 한 번에 집계한다.
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
            return MultiInterviewFeedbackResult.model_validate(
                {"overall": overall, "personas": personas, "feedbacks": feedbacks}
            )
        except ValidationError as exc:
            logger.warning("feedback_multi.dispatch.result_validation_failed", extra={"error": str(exc)})
            raise PipelineError(500, "피드백 결과가 형식을 충족하지 못했습니다.") from exc


def _question_levels(
    assembled: AssembledSession,
    persona_by_question: dict[str, FeedbackPersona],
    raw_result: dict[str, Any],
) -> dict[str, AxisLevels]:
    graded: dict[str, Any] = raw_result["feedbacks"]
    levels: dict[str, AxisLevels] = {}
    for target in assembled.targets:
        level: AxisLevels = graded[target.question_id]["axis_levels"]
        persona = persona_by_question.get(target.question_id)
        if persona is None or persona.role.strip().upper() != _TECH_ROLE:
            # 모델이 값을 줬더라도 비개발 직책 문항의 정확성은 채점에서 뺀다.
            level = level.without_accuracy()
        levels[target.question_id] = level
    return levels


def _session_scores(levels: dict[str, AxisLevels], raw_result: dict[str, Any]) -> SessionScores:
    scores = summarize_session(list(levels.values()), raw_result["consistency"])
    if scores is None:
        # 조립 단계에서 전 문항 미답변은 이미 422 로 걸러진다. 여기 오면 내부 버그다.
        raise PipelineError(500, "피드백 결과가 형식을 충족하지 못했습니다.")
    return scores


def _persona_score_fields(
    persona: FeedbackPersona,
    assembled: AssembledSession,
    persona_by_question: dict[str, FeedbackPersona],
    levels: dict[str, AxisLevels],
) -> dict[str, Any]:
    # 세션 점수와 같은 공식을 그 면접관이 담당한 답변에만 적용한다.
    owned = [
        levels[target.question_id]
        for target in assembled.targets
        if (owner := persona_by_question.get(target.question_id)) is not None and owner.persona_id == persona.persona_id
    ]
    breakdown = compute_breakdown(owned)
    if breakdown is None:
        # 담당 답변이 없으면 점수와 근거 모두 null — "0점"과 "평가 대상 없음"을 구분한다.
        return {"score": None, "score_breakdown": None}
    # 면접관 안에서는 일관성을 판단하지 않는다(담당 문항이 2~3개뿐이다).
    return {"score": breakdown.total_score, "score_breakdown": to_breakdown_payload(breakdown, None)}


def _persona_by_question(job_request: FeedbackMultiRequest) -> dict[str, FeedbackPersona]:
    persona_map = {persona.persona_id: persona for persona in job_request.personas}
    return {
        question.question_id: persona_map[question.persona_id]
        for question in job_request.questions
        if question.persona_id in persona_map
    }


def _persona_id_of(persona_by_question: dict[str, FeedbackPersona], question_id: str) -> str:
    persona = persona_by_question.get(question_id)
    # 조립 단계까지 온 문항은 요청에 있던 질문이고, 요청 검증에서 personaId 존재를 이미 확인했다.
    # 그래도 여기서 터뜨리지 않는 이유는 채점 결과 전체를 잃는 것보다 낫기 때문이다.
    return persona.persona_id if persona is not None else ""


def _failure_payload(job_id: str, session_id: str, status_code: int, message: str) -> dict[str, Any]:
    return FeedbackCallbackFailure(
        job_id=job_id,
        session_id=session_id,
        error=FeedbackErrorDetail(status_code=status_code, message=message),
    ).model_dump(by_alias=True)
