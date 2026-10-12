from __future__ import annotations

import logging
from typing import Any

from app.core.common.applicant_profile.dto import SCHEMA_VERSION
from app.core.common.applicant_profile.evidence import (
    MIN_AVAILABLE_CATEGORIES,
    available_categories,
    evidence_paths,
)
from app.core.common.interview_qa.errors import PipelineError
from app.core.common.interview_qa.ports.anthropic_text_client import (
    AnthropicTextClient,
    AnthropicTextClientError,
)
from app.core.common.question_cycle.dto import CycleQuestion, QuestionCycleRequest
from app.core.common.question_cycle.prompt import SYSTEM_PROMPT, build_cycle_user_message, build_retry_message
from app.core.common.question_cycle.rules import find_violations, parse_questions, sort_questions
from app.core.common.question_cycle.tools import TOOL_NAME, build_submit_question_cycle_tool
from app.core.common.tool_use import extract_tool_input

logger = logging.getLogger(__name__)

_GENERATE_FAILURE_MESSAGE = "면접 질문 생성에 실패했습니다. 잠시 후 다시 시도해 주세요."


class QuestionCycleGenerate:
    """종합 데이터만 보고 한 모드의 원질문 사이클을 만든다.

    LLM 은 1회 호출한다. 구성 규칙을 어기면 위반 내용을 붙여 retry 번까지 다시 부르고,
    그래도 어기면 실패(500) 다. 일부만 맞는 사이클을 내보내면 세트 구성이 깨진다.
    """

    def __init__(
        self,
        client: AnthropicTextClient,
        text_model: str,
        max_tokens: int,
        retry: int,
        expected_answer_max_chars: int,
        exclude_max: int,
        text_max_chars: int,
    ) -> None:
        self._client = client
        self._model = text_model
        self._max_tokens = max_tokens
        self._retry = retry
        self._expected_answer_max_chars = expected_answer_max_chars
        self._exclude_max = exclude_max
        self._text_max_chars = text_max_chars

    async def execute(self, request: QuestionCycleRequest) -> list[CycleQuestion]:
        profile = request.profile
        if profile.schema_version != SCHEMA_VERSION:
            raise PipelineError(422, f"지원하지 않는 종합 데이터 버전입니다(schemaVersion={profile.schema_version}).")

        # 저장소 트리가 없으므로 파일이 실제로 있는지는 보지 않는다. 그건 /profile 의 근거 정리가 보장한다.
        evidence = evidence_paths(profile)
        if not evidence:
            raise PipelineError(422, "종합 데이터에 근거 경로가 없습니다.")
        available = available_categories(profile)
        if len(available) < MIN_AVAILABLE_CATEGORIES:
            raise PipelineError(422, "종합 데이터에서 질문을 만들 수 있는 카테고리가 부족합니다.")

        exclude = request.exclude_questions[: self._exclude_max]
        if len(request.exclude_questions) > self._exclude_max:
            logger.warning(
                "question_cycle.generate.exclude_truncated",
                extra={"received": len(request.exclude_questions), "max": self._exclude_max},
            )

        tool = build_submit_question_cycle_tool(available)
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": build_cycle_user_message(
                    profile, request.mode, available, evidence, exclude, self._text_max_chars
                ),
            }
        ]

        for attempt in range(self._retry + 1):
            content_blocks = await self._call(messages, tool)
            questions, violations = parse_questions(
                extract_tool_input(content_blocks, TOOL_NAME),
                self._expected_answer_max_chars,
            )
            violations.extend(find_violations(questions, request.mode, available, evidence))
            if not violations:
                logger.info(
                    "question_cycle.generate.done",
                    extra={"mode": request.mode, "attempt": attempt + 1, "question_count": len(questions)},
                )
                return sort_questions(questions)

            logger.warning(
                "question_cycle.generate.violations",
                extra={"mode": request.mode, "attempt": attempt + 1, "violations": violations},
            )
            messages.extend(_retry_turn(content_blocks, violations))

        raise PipelineError(500, "면접 질문이 구성 규칙을 충족하지 못했습니다.")

    async def _call(self, messages: list[dict[str, Any]], tool: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            response = await self._client.call(
                model=self._model,
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=[tool],
                # 도구 호출을 강제해 스키마 밖 형태로 답할 여지를 없앤다.
                tool_choice={"type": "tool", "name": TOOL_NAME},
                max_tokens=self._max_tokens,
            )
        except AnthropicTextClientError as exc:
            logger.warning("question_cycle.generate.call_failed", exc_info=True)
            raise PipelineError(500, _GENERATE_FAILURE_MESSAGE) from exc

        if response.stop_reason == "max_tokens":
            # 응답이 잘리면 tool_use JSON 도 깨져 검증에서 걸린다.
            # 원인이 토큰 부족이라는 걸 로그에서 알 수 있어야 한다(SOLO 15문항이 가장 길다).
            logger.warning("question_cycle.generate.truncated", extra={"max_tokens": self._max_tokens})
        return response.content_blocks


def _retry_turn(content_blocks: list[dict[str, Any]], violations: list[str]) -> list[dict[str, Any]]:
    """직전 제출을 대화에 남기고 위반 내용을 돌려준다.

    도구 호출 뒤에는 같은 id 의 tool_result 가 와야 API 가 받아 준다.
    """
    retry_message = build_retry_message(violations)
    tool_use = next((block for block in content_blocks if block.get("type") == "tool_use"), None)
    if tool_use is None:
        return [
            {"role": "assistant", "content": content_blocks or [{"type": "text", "text": "(응답 없음)"}]},
            {"role": "user", "content": retry_message},
        ]
    return [
        {"role": "assistant", "content": content_blocks},
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": str(tool_use.get("id", "")),
                    "content": retry_message,
                    "is_error": True,
                }
            ],
        },
    ]
