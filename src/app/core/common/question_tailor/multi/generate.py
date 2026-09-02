from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from app.core.common.interview_qa.dto import ProjectSummary
from app.core.common.interview_qa.errors import PipelineError
from app.core.common.interview_qa.ports.anthropic_text_client import (
    AnthropicTextClient,
    AnthropicTextClientError,
)
from app.core.common.question_tailor.dto import OriginalQuestion
from app.core.common.question_tailor.multi.dto import GeneratedQuestion, TailorPersona
from app.core.common.question_tailor.multi.prompt import SYSTEM_PROMPT, build_generate_user_message
from app.core.common.question_tailor.multi.tools import SUBMIT_GENERATED_QUESTIONS_TOOL
from app.core.common.tool_use import extract_tool_input

logger = logging.getLogger(__name__)

_TOOL_NAME = "submit_generated_questions"

# 질문 본문 상한. 프롬프트에도 200자로 못박지만 모델이 넘길 수 있어 서버에서 한 번 더 막는다.
# interview_question.content 가 VARCHAR(255) 라 여기서 걸러야 소켓 서버 저장이 안 깨진다.
_QUESTION_MAX_CHARS = 250


class MultiQuestionGenerate:
    """비개발 면접관이 물을 질문을 새로 만든다. N:1 테일러의 1단계.

    solo 재작성과 달리 실패 시 폴백이 없다. 생성 결과가 전체 문항의 대부분이라
    빈손으로 면접을 열면 2문항짜리 면접이 되기 때문이다.
    """

    def __init__(
        self,
        client: AnthropicTextClient,
        text_model: str,
        max_tokens: int,
        text_max_chars: int,
    ) -> None:
        self._client = client
        self._model = text_model
        self._max_tokens = max_tokens
        self._text_max_chars = text_max_chars

    async def execute(
        self,
        personas: Sequence[TailorPersona],
        project_summary: ProjectSummary,
        tech_questions: Sequence[OriginalQuestion],
    ) -> tuple[GeneratedQuestion, ...]:
        user_message = build_generate_user_message(
            personas,
            project_summary,
            tech_questions,
            self._text_max_chars,
        )

        try:
            response = await self._client.call(
                model=self._model,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
                tools=[SUBMIT_GENERATED_QUESTIONS_TOOL],
                # 도구 호출을 강제해 스키마 밖 형태로 답할 여지를 없앤다.
                tool_choice={"type": "tool", "name": _TOOL_NAME},
                max_tokens=self._max_tokens,
            )
        except AnthropicTextClientError as exc:
            raise PipelineError(502, "면접 질문 생성에 실패했습니다. 잠시 후 다시 시도해 주세요.") from exc

        if response.stop_reason == "max_tokens":
            # 응답이 잘리면 tool_use JSON 도 깨져 아래 파싱이 실패한다.
            # 원인이 토큰 부족이라는 걸 로그에서 알 수 있어야 한다.
            logger.warning(
                "question_tailor_multi.generate.truncated",
                extra={"max_tokens": self._max_tokens, "persona_count": len(personas)},
            )

        return _parse_submission(response.content_blocks, personas)


def _parse_submission(
    content_blocks: list[dict[str, Any]],
    personas: Sequence[TailorPersona],
) -> tuple[GeneratedQuestion, ...]:
    raw = extract_tool_input(content_blocks, _TOOL_NAME)
    if raw is None:
        raise PipelineError(500, "면접 질문 생성 결과를 해석하지 못했습니다.")

    entries = raw.get("questions")
    if not isinstance(entries, list):
        raise PipelineError(500, "면접 질문 생성 결과가 형식을 충족하지 못했습니다.")

    by_index: dict[int, list[GeneratedQuestion]] = {index: [] for index in range(1, len(personas) + 1)}
    for entry in entries:
        parsed = _parse_entry(entry, personas)
        if parsed is None:
            continue
        index, question = parsed
        by_index[index].append(question)

    _check_counts(by_index, personas)

    # 면접관 순서대로 평탄화한다. 이 순서가 그대로 면접 진행 순서가 된다.
    # 초과분은 버린다 — 개수 검사는 부족한 경우만 막는다.
    return tuple(
        question
        for index in range(1, len(personas) + 1)
        for question in by_index[index][: personas[index - 1].question_count]
    )


def _parse_entry(
    entry: object,
    personas: Sequence[TailorPersona],
) -> tuple[int, GeneratedQuestion] | None:
    if not isinstance(entry, dict):
        return None

    index = entry.get("persona_index")
    if not isinstance(index, int) or not 1 <= index <= len(personas):
        # 없는 면접관 번호를 지어낸 경우. 버리고 계속 진행한다 — 개수 검사에서 걸린다.
        logger.warning("question_tailor_multi.generate.unknown_persona_index", extra={"persona_index": index})
        return None

    question = _clean_text(entry.get("question"))
    expected_answer = _clean_text(entry.get("expected_answer"))
    category = _clean_text(entry.get("category"))
    if question is None or expected_answer is None or category is None:
        logger.warning("question_tailor_multi.generate.incomplete_entry", extra={"persona_index": index})
        return None

    persona = personas[index - 1]
    return index, GeneratedQuestion(
        persona_id=persona.persona_id,
        category=category,
        question=question[:_QUESTION_MAX_CHARS],
        expected_answer=expected_answer,
        # based_on 은 비어 올 수 있다. InterviewItem 포맷을 맞추려면 최소 1개가 필요하므로
        # 직책을 특수값으로 채운다(/generate 가 ["file_tree"] 를 쓰는 것과 같은 방식).
        based_on=_clean_based_on(entry.get("based_on")) or [f"persona:{persona.role.strip().lower()}"],
    )


def _check_counts(
    by_index: dict[int, list[GeneratedQuestion]],
    personas: Sequence[TailorPersona],
) -> None:
    short = [index for index, questions in by_index.items() if len(questions) < personas[index - 1].question_count]
    if not short:
        return
    # 일부만 채워 내보내면 어떤 면접관은 물을 질문이 없는 채로 면접이 열린다.
    # 원질문 폴백이 가능한 solo 와 달리 여기서는 되돌릴 것이 없으므로 실패로 알린다.
    logger.warning(
        "question_tailor_multi.generate.missing_questions",
        extra={"short_personas": [personas[index - 1].role for index in short]},
    )
    raise PipelineError(500, "일부 면접관의 질문이 생성되지 않았습니다.")


def _clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _clean_based_on(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]
