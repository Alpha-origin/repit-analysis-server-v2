from __future__ import annotations

import logging
from typing import Any

from app.core.common.feedback.solo.dto import AssembledSession
from app.core.common.feedback.solo.prompt import SYSTEM_PROMPT, build_grading_user_message
from app.core.common.feedback.solo.tools import SUBMIT_FEEDBACK_TOOL
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
    ) -> dict[str, Any]:
        user_message = build_grading_user_message(assembled, persona_type, self._answer_max_chars)

        try:
            response = await self._client.call(
                model=self._model,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
                tools=[SUBMIT_FEEDBACK_TOOL],
                # 도구 호출을 강제해 스키마 밖 형태로 답할 여지를 없앤다.
                tool_choice={"type": "tool", "name": _TOOL_NAME},
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

        expected_ids = tuple(target.question_id for target in assembled.targets)
        return _parse_submission(response.content_blocks, expected_ids)


def _parse_submission(
    content_blocks: list[dict[str, Any]],
    expected_ids: tuple[str, ...],
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
            logger.warning(
                "feedback_solo.grading.unknown_question_id",
                extra={"question_id": question_id},
            )
            continue
        by_id[question_id] = entry

    missing = [question_id for question_id in expected_ids if question_id not in by_id]
    if missing:
        # 이 검사가 없으면 15문항 보냈는데 피드백 12개만 담긴 응답이 그대로 나간다.
        logger.warning("feedback_solo.grading.missing_feedback", extra={"missing_count": len(missing)})
        raise PipelineError(500, "일부 문항의 피드백이 생성되지 않았습니다.")

    return {"overall": overall, "feedbacks": by_id}
