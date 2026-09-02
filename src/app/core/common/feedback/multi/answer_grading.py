from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from app.core.common.feedback.multi.dto import FeedbackPersona
from app.core.common.feedback.multi.prompt import SYSTEM_PROMPT, build_grading_user_message
from app.core.common.feedback.multi.tools import SUBMIT_MULTI_FEEDBACK_TOOL
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

    문항 수가 최대 9개(원질문 6 + 꼬리 3) 라 1:1(최대 7개) 과 큰 차이가 없어서
    호출을 나눌 이유가 없다. 나누면 오히려 면접관 간 비교가 불가능해진다.
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
    ) -> dict[str, Any]:
        user_message = build_grading_user_message(
            assembled,
            personas,
            persona_by_question,
            self._answer_max_chars,
        )

        try:
            response = await self._client.call(
                model=self._model,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
                tools=[SUBMIT_MULTI_FEEDBACK_TOOL],
                # 도구 호출을 강제해 스키마 밖 형태로 답할 여지를 없앤다.
                tool_choice={"type": "tool", "name": _TOOL_NAME},
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

        expected_question_ids = tuple(target.question_id for target in assembled.targets)
        expected_persona_ids = tuple(persona.persona_id for persona in personas)
        return _parse_submission(response.content_blocks, expected_question_ids, expected_persona_ids)


def _parse_submission(
    content_blocks: list[dict[str, Any]],
    expected_question_ids: tuple[str, ...],
    expected_persona_ids: tuple[str, ...],
) -> dict[str, Any]:
    raw = extract_tool_input(content_blocks, _TOOL_NAME)
    if raw is None:
        raise PipelineError(500, "피드백 생성 결과를 해석하지 못했습니다.")

    overall = raw.get("overall")
    feedbacks = raw.get("feedbacks")
    personas = raw.get("personas")
    if not isinstance(overall, dict) or not isinstance(feedbacks, list) or not isinstance(personas, list):
        raise PipelineError(500, "피드백 생성 결과가 형식을 충족하지 못했습니다.")

    return {
        "overall": overall,
        "feedbacks": _index_by(feedbacks, "question_id", expected_question_ids, "question"),
        "personas": _index_by(personas, "persona_id", expected_persona_ids, "persona"),
    }


def _index_by(
    entries: list[Any],
    key_field: str,
    expected_ids: tuple[str, ...],
    label: str,
) -> dict[str, dict[str, Any]]:
    expected = set(expected_ids)
    by_id: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key = entry.get(key_field)
        if not isinstance(key, str) or key not in expected:
            # 전달하지 않은 id 를 지어낸 경우. 버리고 계속 진행한다 — 누락 검사에서 걸린다.
            logger.warning("feedback_multi.grading.unknown_id", extra={"field": key_field, "value": key})
            continue
        by_id[key] = entry

    missing = [key for key in expected_ids if key not in by_id]
    if missing:
        # 이 검사가 없으면 면접관 3명 중 2명만 담긴 응답이 그대로 나간다.
        logger.warning(
            "feedback_multi.grading.missing_entries",
            extra={"label": label, "missing_count": len(missing)},
        )
        raise PipelineError(500, "일부 채점 결과가 생성되지 않았습니다.")
    return by_id
