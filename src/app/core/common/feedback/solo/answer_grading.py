from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from app.core.common.feedback.depth import evidence_sources
from app.core.common.feedback.grading_retry import GRADING_ATTEMPTS, IncompleteGradingError, log_dropped, unknown_key
from app.core.common.feedback.question_label import (
    FEEDBACK_TEXT_FIELDS,
    OVERALL_TEXT_FIELDS,
    field_texts,
    warn_out_of_range_labels,
)
from app.core.common.feedback.scoring import grading_drop_reason, parse_consistency, parse_grading_levels
from app.core.common.feedback.solo.dto import AssembledSession
from app.core.common.feedback.solo.prompt import build_grading_user_message, system_prompt_for_job
from app.core.common.feedback.solo.tools import feedback_tool_for_job
from app.core.common.interview_qa.errors import PipelineError
from app.core.common.interview_qa.ports.anthropic_text_client import (
    AnthropicTextClient,
    AnthropicTextClientError,
)
from app.core.common.tool_use import extract_tool_input

logger = logging.getLogger(__name__)

_TOOL_NAME = "submit_feedback"


class AnswerGrading:
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
        persona_type: str | None,
        persona_tone: str | None = None,
        job_role: str | None = None,
        job_title: str | None = None,
    ) -> dict[str, Any]:
        user_message = build_grading_user_message(
            assembled,
            persona_type,
            self._answer_max_chars,
            persona_tone=persona_tone,
            job_title=job_title,
        )

        expected_ids = tuple(target.question_id for target in assembled.targets)
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
                    system=system_prompt_for_job(job_role),
                    messages=[{"role": "user", "content": user_message}],
                    tools=[feedback_tool_for_job(job_role)],
                    # Haiku 5.5는 강제 도구 호출 시 추론을 생략한다.
                    # 자동 선택으로 채점 전 추론을 허용하고 입력 형식은 strict 스키마로 보장한다.
                    tool_choice={"type": "auto"},
                    max_tokens=self._max_tokens,
                )
            except AnthropicTextClientError as exc:
                # 채점은 이 파이프라인의 산출물 그 자체다. 트리아지처럼 빈 결과로 진행하면
                # 사용자는 "0점"으로 오해한다. 실패 콜백으로 알린다.
                raise PipelineError(502, "피드백 생성에 실패했습니다. 잠시 후 다시 시도해 주세요.") from exc

            if response.stop_reason == "max_tokens":
                # 응답이 잘리면 tool_use JSON 도 깨져 아래 파싱이 실패한다.
                # 원인이 토큰 부족이라는 걸 로그에서 알 수 있어야 한다.
                logger.warning(
                    "feedback_solo.grading.truncated",
                    extra={"max_tokens": self._max_tokens, "target_count": len(assembled.targets)},
                )
            try:
                return _parse_submission(response.content_blocks, expected_ids, sources, attempt)
            except IncompleteGradingError:
                # 잘린 응답은 같은 조건으로 다시 불러도 또 잘린다. 다시 부르지 않는다.
                if attempt == GRADING_ATTEMPTS or response.stop_reason == "max_tokens":
                    raise
                logger.warning("feedback_solo.grading.retry attempt=%d", attempt, extra={"attempt": attempt})
        raise AssertionError("unreachable")


def _parse_submission(
    content_blocks: list[dict[str, Any]],
    expected_ids: tuple[str, ...],
    sources_by_question: Mapping[str, tuple[str, ...]],
    attempt: int = 1,
) -> dict[str, Any]:
    raw = extract_tool_input(content_blocks, _TOOL_NAME)
    if raw is None:
        raise PipelineError(500, "피드백 생성 결과를 해석하지 못했습니다.")

    overall = raw.get("overall")
    feedbacks = raw.get("feedbacks")
    if not isinstance(overall, dict) or not isinstance(feedbacks, list):
        raise PipelineError(500, "피드백 생성 결과가 형식을 충족하지 못했습니다.")

    expected = set(expected_ids)
    by_id: dict[str, dict[str, Any]] = {}
    for entry in feedbacks:
        if not isinstance(entry, dict):
            continue
        question_id = entry.get("question_id")
        if not isinstance(question_id, str) or question_id not in expected:
            # 전달하지 않은 id 를 지어낸 경우. 버리고 계속 진행한다.
            log_dropped(
                logger, "feedback_solo.grading.dropped", unknown_key(question_id), "unknown_question_id", attempt
            )
            continue
        sources = sources_by_question.get(question_id, ())
        levels = parse_grading_levels(entry.get("scores"), entry.get("depth_evidence"), sources)
        if levels is None:
            # 등급이 없거나 범위 밖, 또는 인용이 원문에 없으면 점수를 계산할 수 없다. 아래 누락 검사에서 걸린다.
            reason = grading_drop_reason(entry.get("scores"), entry.get("depth_evidence"), sources)
            log_dropped(logger, "feedback_solo.grading.dropped", question_id, reason, attempt, entry.get("scores"))
            continue
        by_id[question_id] = {**entry, "axis_levels": levels}

    missing = [question_id for question_id in expected_ids if question_id not in by_id]
    if missing:
        # 이 검사가 없으면 15문항 보냈는데 피드백 12개만 담긴 응답이 그대로 나간다.
        logger.warning("feedback_solo.grading.missing_feedback", extra={"missing_count": len(missing)})
        raise IncompleteGradingError(500, "일부 문항의 피드백이 생성되지 않았습니다.")

    # 문장에 블록 번호(Q1…) 대신 question_id 를 쓴 흔적을 찾는다. 로그만 남기고 결과는 그대로 쓴다.
    warn_out_of_range_labels(
        logger,
        "feedback_solo.grading.question_id_label",
        field_texts([overall], OVERALL_TEXT_FIELDS) + field_texts(by_id.values(), FEEDBACK_TEXT_FIELDS),
        len(expected_ids),
    )
    return {
        "overall": overall,
        "feedbacks": by_id,
        # 일관성은 종합 점수에 들어가지 않는 보조 지표라, 판단 불가·형식 오류면 None 으로 두고 진행한다.
        "consistency": parse_consistency(overall.get("consistency")),
    }
