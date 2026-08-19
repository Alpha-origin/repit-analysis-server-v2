from __future__ import annotations

import logging
from typing import Any

from app.core.common.interview_qa.errors import PipelineError
from app.core.common.interview_qa.ports.anthropic_text_client import (
    AnthropicTextClient,
    AnthropicTextClientError,
)
from app.core.common.question_tailor.dto import QuestionTailorRequest, TailoredQuestion
from app.core.common.question_tailor.prompt import SYSTEM_PROMPT, build_rewrite_user_message
from app.core.common.question_tailor.tools import SUBMIT_TAILORED_QUESTIONS_TOOL
from app.core.common.tool_use import extract_tool_input

logger = logging.getLogger(__name__)

_TOOL_NAME = "submit_tailored_questions"


class QuestionRewrite:
    def __init__(
        self,
        client: AnthropicTextClient,
        text_model: str,
        max_tokens: int,
        question_max_chars: int,
    ) -> None:
        self._client = client
        self._model = text_model
        self._max_tokens = max_tokens
        self._question_max_chars = question_max_chars

    async def execute(self, job_request: QuestionTailorRequest) -> tuple[TailoredQuestion, ...] | None:
        """재작성 결과를 돌려준다. 재작성에 실패하면 None — 호출측이 원질문으로 폴백한다.

        LLM 호출·파싱 실패는 예외로 올리지 않는다. 원질문은 이미 유효한 산출물이라
        면접을 못 열게 만드는 것보다 원문을 그대로 쓰는 편이 낫다.
        """
        if not job_request.profile.has_any:
            # 개인화 축이 하나도 없으면 재작성 자체가 성립하지 않는다. 호출측 버그이므로 실패로 알린다.
            raise PipelineError(422, "질문을 재작성할 사전 정보가 없습니다.")

        user_message = build_rewrite_user_message(job_request, self._question_max_chars)

        try:
            response = await self._client.call(
                model=self._model,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
                tools=[SUBMIT_TAILORED_QUESTIONS_TOOL],
                # 도구 호출을 강제해 스키마 밖 형태로 답할 여지를 없앤다.
                tool_choice={"type": "tool", "name": _TOOL_NAME},
                max_tokens=self._max_tokens,
            )
        except AnthropicTextClientError:
            logger.warning("question_tailor.rewrite.call_failed", exc_info=True)
            return None

        if response.stop_reason == "max_tokens":
            # 응답이 잘리면 tool_use JSON 도 깨져 아래 파싱이 실패한다.
            # 원인이 토큰 부족이라는 걸 로그에서 알 수 있어야 한다.
            logger.warning(
                "question_tailor.rewrite.truncated",
                extra={"max_tokens": self._max_tokens, "question_count": len(job_request.questions)},
            )

        expected_ids = tuple(question.id for question in job_request.questions)
        return _parse_submission(response.content_blocks, expected_ids)


def _parse_submission(
    content_blocks: list[dict[str, Any]],
    expected_ids: tuple[int, ...],
) -> tuple[TailoredQuestion, ...] | None:
    raw = extract_tool_input(content_blocks, _TOOL_NAME)
    if raw is None:
        logger.warning("question_tailor.rewrite.tool_input_missing")
        return None

    questions = raw.get("questions")
    if not isinstance(questions, list):
        logger.warning("question_tailor.rewrite.malformed_result")
        return None

    expected = set(expected_ids)
    by_id: dict[int, str] = {}
    for entry in questions:
        if not isinstance(entry, dict):
            continue
        question_id = entry.get("id")
        content = entry.get("question")
        if not isinstance(question_id, int) or question_id not in expected:
            # 전달하지 않은 id 를 지어낸 경우. 버리고 계속 진행한다.
            logger.warning("question_tailor.rewrite.unknown_id", extra={"question_id": question_id})
            continue
        if not isinstance(content, str) or not content.strip():
            # 빈 본문은 재작성이 안 된 것과 같다. 아래 누락 검사에서 걸리게 둔다.
            continue
        by_id[question_id] = content.strip()

    missing = [question_id for question_id in expected_ids if question_id not in by_id]
    if missing:
        # 일부만 채워 내보내면 재작성된 질문과 원질문이 한 면접에 섞여 어조가 들쭉날쭉해진다.
        # 부분 폴백보다 전체 폴백이 예측 가능하다.
        logger.warning("question_tailor.rewrite.missing_questions", extra={"missing_count": len(missing)})
        return None

    # 순서는 LLM 응답 순서가 아니라 요청에 실려온 원질문 순서를 따른다.
    return tuple(TailoredQuestion(id=question_id, question=by_id[question_id]) for question_id in expected_ids)
