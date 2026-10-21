"""운영과 같은 누락 재시도 정책으로 평가한다."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from evaluation.competency_run import capture_validation

from app.core.common.feedback.depth import evidence_sources
from app.core.common.feedback.grading_retry import GRADING_ATTEMPTS, IncompleteGradingError
from app.core.common.feedback.multi.answer_grading import _parse_submission as parse_multi
from app.core.common.feedback.solo.answer_grading import _parse_submission as parse_solo
from app.core.common.feedback.solo.dto import AssembledSession
from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicTextClient


def validate_legacy(
    case: dict[str, Any], assembled: AssembledSession, blocks: list[dict[str, Any]], attempt: int
) -> None:
    ids = tuple(t.question_id for t in assembled.targets)
    sources = {
        t.question_id: evidence_sources(t.answer, t.parent_answer if t.type == "FOLLOW" else None, 3000)
        for t in assembled.targets
    }
    if case["mode"] == "solo":
        parse_solo(blocks, ids, sources, attempt)
    else:
        parse_multi(blocks, ids, tuple(p["id"] for p in case["personas"]), sources, attempt)


async def evaluate(
    client: AnthropicTextClient,
    case: dict[str, Any],
    assembled: AssembledSession,
    record: dict[str, Any],
    system: str,
    user_message: str,
    tool: dict[str, Any],
    parse: Callable[[dict[str, Any], list[dict[str, Any]], str, str], dict[str, Any]],
) -> dict[str, Any]:
    record.update({"input_tokens": 0, "output_tokens": 0})
    for attempt in range(1, GRADING_ATTEMPTS + 1):
        response = await client.call(
            model=record["model"],
            system=system,
            messages=[{"role": "user", "content": user_message}],
            tools=[tool],
            tool_choice={"type": "auto"},
            max_tokens=record["max_tokens"],
        )
        record.update(
            {"attempts": attempt, "stop_reason": response.stop_reason, "raw_content_blocks": response.content_blocks}
        )
        record["input_tokens"] += response.input_tokens
        record["output_tokens"] += response.output_tokens
        try:
            with capture_validation(record):
                if record["scoring_version"] == "axis-v4":
                    validate_legacy(case, assembled, response.content_blocks, attempt)
                return parse(case, response.content_blocks, tool["name"], record["scoring_version"])
        except IncompleteGradingError:
            if attempt == GRADING_ATTEMPTS or response.stop_reason == "max_tokens":
                raise
    raise AssertionError("unreachable")
