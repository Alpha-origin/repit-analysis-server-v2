from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from app.core.common.feedback.depth import evidence_sources
from app.core.common.feedback.grading_retry import GRADING_ATTEMPTS, IncompleteGradingError, log_dropped, unknown_key
from app.core.common.feedback.multi.dto import FeedbackPersona
from app.core.common.feedback.multi.prompt import build_grading_user_message, system_prompt_for_job
from app.core.common.feedback.multi.tools import feedback_tool_for_job
from app.core.common.feedback.question_label import (
    FEEDBACK_TEXT_FIELDS,
    OVERALL_TEXT_FIELDS,
    PERSONA_TEXT_FIELDS,
    field_texts,
    warn_out_of_range_labels,
)
from app.core.common.feedback.scoring import grading_drop_reason, parse_consistency, parse_grading_levels
from app.core.common.feedback.solo.dto import AssembledSession
from app.core.common.interview_qa.errors import PipelineError
from app.core.common.interview_qa.ports.anthropic_text_client import (
    AnthropicTextClient,
    AnthropicTextClientError,
)
from app.core.common.tool_use import extract_tool_input

logger = logging.getLogger(__name__)

_TOOL_NAME = "submit_multi_feedback"


class MultiAnswerGrading:
    """N:1 채점. 전 문항 + 면접관별 평가 + 종합을 1회 LLM 호출로 받는다.

    신규 면접은 기본 원질문 4/6/8개에 꼬리질문이 더해진다.
    기존 5인 면접도 처리하며, 최대 구성의 토큰 여유는 별도 실측으로 검증한다.
    """

    def __init__(
        self,
        client: AnthropicTextClient,
        text_model: str,
        max_tokens: int,
        answer_max_chars: int,
    ) -> None:
        self._client = client
        self._model = text_model
        self._max_tokens = max_tokens
        self._answer_max_chars = answer_max_chars

    async def execute(
        self,
        assembled: AssembledSession,
        personas: Sequence[FeedbackPersona],
        persona_by_question: Mapping[str, FeedbackPersona],
        job_role: str | None = None,
        job_title: str | None = None,
    ) -> dict[str, Any]:
        user_message = build_grading_user_message(
            assembled,
            personas,
            persona_by_question,
            self._answer_max_chars,
        )

        expected_question_ids = tuple(target.question_id for target in assembled.targets)
        expected_persona_ids = tuple(persona.persona_id for persona in personas)
        sources = {
            target.question_id: evidence_sources(
                target.answer,
                target.parent_answer if target.type == "FOLLOW" and target.parent_question is not None else None,
                self._answer_max_chars,
            )
            for target in assembled.targets
        }
        for attempt in range(1, GRADING_ATTEMPTS + 1):
            try:
                response = await self._client.call(
                    model=self._model,
                    system=system_prompt_for_job(job_role, job_title),
                    messages=[{"role": "user", "content": user_message}],
                    tools=[feedback_tool_for_job(job_role)],
                    # 강제 호출 대신 채점 전 추론을 허용한다. 입력 형식은 strict 스키마로 보장한다.
                    tool_choice={"type": "auto"},
                    max_tokens=self._max_tokens,
                )
            except AnthropicTextClientError as exc:
                # 채점은 이 파이프라인의 산출물 그 자체다. 빈 결과로 진행하면
                # 사용자는 "0점"으로 오해한다. 실패 콜백으로 알린다.
                raise PipelineError(502, "피드백 생성에 실패했습니다. 잠시 후 다시 시도해 주세요.") from exc

            if response.stop_reason == "max_tokens":
                # 응답이 잘리면 tool_use JSON 도 깨져 아래 파싱이 실패한다.
                # 원인이 토큰 부족이라는 걸 로그에서 알 수 있어야 한다.
                logger.warning(
                    "feedback_multi.grading.truncated",
                    extra={
                        "max_tokens": self._max_tokens,
                        "target_count": len(assembled.targets),
                        "persona_count": len(personas),
                    },
                )
            try:
                return _parse_submission(
                    response.content_blocks, expected_question_ids, expected_persona_ids, sources, attempt
                )
            except IncompleteGradingError:
                # 잘린 응답은 같은 조건으로 다시 불러도 또 잘린다. 다시 부르지 않는다.
                if attempt == GRADING_ATTEMPTS or response.stop_reason == "max_tokens":
                    raise
                logger.warning("feedback_multi.grading.retry attempt=%d", attempt, extra={"attempt": attempt})
        raise AssertionError("unreachable")


def _parse_submission(
    content_blocks: list[dict[str, Any]],
    expected_question_ids: tuple[str, ...],
    expected_persona_ids: tuple[str, ...],
    sources_by_question: Mapping[str, tuple[str, ...]],
    attempt: int = 1,
) -> dict[str, Any]:
    raw = extract_tool_input(content_blocks, _TOOL_NAME)
    if raw is None:
        raise PipelineError(500, "피드백 생성 결과를 해석하지 못했습니다.")

    overall = raw.get("overall")
    feedbacks = raw.get("feedbacks")
    personas = raw.get("personas")
    if not isinstance(overall, dict) or not isinstance(feedbacks, list) or not isinstance(personas, list):
        raise PipelineError(500, "피드백 생성 결과가 형식을 충족하지 못했습니다.")

    graded_feedbacks = _index_by(
        _with_axis_levels(feedbacks, sources_by_question, attempt),
        "question_id",
        expected_question_ids,
        "question",
        attempt,
    )
    graded_personas = _index_by(personas, "persona_id", expected_persona_ids, "persona", attempt)
    # 문장에 블록 번호(Q1…) 대신 question_id 를 쓴 흔적을 찾는다. 로그만 남기고 결과는 그대로 쓴다.
    warn_out_of_range_labels(
        logger,
        "feedback_multi.grading.question_id_label",
        field_texts([overall], OVERALL_TEXT_FIELDS)
        + field_texts(graded_feedbacks.values(), FEEDBACK_TEXT_FIELDS)
        + field_texts(graded_personas.values(), PERSONA_TEXT_FIELDS),
        len(expected_question_ids),
    )
    return {
        "overall": overall,
        "feedbacks": graded_feedbacks,
        "personas": graded_personas,
        # 일관성은 종합 점수에 들어가지 않는 보조 지표라, 판단 불가·형식 오류면 None 으로 두고 진행한다.
        "consistency": parse_consistency(overall.get("consistency")),
    }


def _with_axis_levels(
    entries: list[Any], sources_by_question: Mapping[str, tuple[str, ...]], attempt: int = 1
) -> list[Any]:
    # 등급이 없거나 범위 밖인 문항은 점수를 계산할 수 없으므로 버린다 — 누락 검사에서 걸린다.
    parsed: list[Any] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        question_id = entry.get("question_id")
        sources = sources_by_question.get(question_id, ()) if isinstance(question_id, str) else ()
        levels = parse_grading_levels(entry.get("scores"), entry.get("depth_evidence"), sources)
        if levels is None:
            reason = grading_drop_reason(entry.get("scores"), entry.get("depth_evidence"), sources)
            # 아직 id 를 검증하기 전이라, 채점 대상 id 가 아니면 값 대신 형태만 남긴다.
            key = question_id if isinstance(question_id, str) and question_id in sources_by_question else None
            log_dropped(
                logger,
                "feedback_multi.grading.dropped",
                key or unknown_key(question_id),
                reason,
                attempt,
                entry.get("scores"),
            )
            continue
        parsed.append({**entry, "axis_levels": levels})
    return parsed


def _index_by(
    entries: list[Any],
    key_field: str,
    expected_ids: tuple[str, ...],
    label: str,
    attempt: int = 1,
) -> dict[str, dict[str, Any]]:
    expected = set(expected_ids)
    by_id: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key = entry.get(key_field)
        if not isinstance(key, str) or key not in expected:
            # 전달하지 않은 id 를 지어낸 경우. 버리고 계속 진행한다 — 누락 검사에서 걸린다.
            log_dropped(logger, "feedback_multi.grading.dropped", unknown_key(key), f"unknown_{key_field}", attempt)
            continue
        by_id[key] = entry

    missing = [key for key in expected_ids if key not in by_id]
    if missing:
        # 이 검사가 없으면 면접관 3명 중 2명만 담긴 응답이 그대로 나간다.
        logger.warning(
            "feedback_multi.grading.missing_entries",
            extra={"label": label, "missing_count": len(missing)},
        )
        raise IncompleteGradingError(500, "일부 채점 결과가 생성되지 않았습니다.")
    return by_id
