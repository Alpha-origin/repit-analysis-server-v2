from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from app.core.common.company.dto import CompanyContext
from app.core.common.company.fit.dto import (
    FIT_COMMENT_MAX,
    FIT_ITEM_TEXT_MAX,
    FIT_ITEMS_MAX,
    CompanyFitGap,
    CompanyFitItem,
    CompanyFitPayload,
)
from app.core.common.company.fit.prompt import SYSTEM_PROMPT, build_fit_user_message
from app.core.common.company.fit.tools import SUBMIT_COMPANY_FIT_TOOL, TOOL_NAME
from app.core.common.feedback.solo.dto import GradingTarget
from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicTextClient
from app.core.common.tool_use import extract_tool_input

logger = logging.getLogger(__name__)


class CompanyFitEvaluation:
    def __init__(
        self,
        client: AnthropicTextClient,
        text_model: str,
        max_tokens: int,
        temperature: float,
        answer_max_chars: int,
    ) -> None:
        self._client = client
        self._model = text_model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._answer_max_chars = answer_max_chars

    async def execute(
        self,
        targets: Sequence[GradingTarget],
        company: CompanyContext,
        persona_tone: str | None,
    ) -> CompanyFitPayload | None:
        response = await self._client.call(
            model=self._model,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": build_fit_user_message(targets, company, persona_tone, self._answer_max_chars),
                }
            ],
            tools=[SUBMIT_COMPANY_FIT_TOOL],
            tool_choice={"type": "tool", "name": TOOL_NAME},
            max_tokens=self._max_tokens,
            temperature=self._temperature,
        )
        raw = extract_tool_input(response.content_blocks, TOOL_NAME)
        if raw is None or response.stop_reason == "max_tokens":
            logger.warning("company_fit.submission_missing_or_truncated")
            return None
        return _parse_submission(raw, company, {target.question_id for target in targets})


def _parse_submission(
    raw: dict[str, Any],
    company: CompanyContext,
    question_ids: set[str],
) -> CompanyFitPayload | None:
    raw_comment = raw.get("comment")
    if isinstance(raw_comment, str) and len(raw_comment.strip()) > FIT_COMMENT_MAX:
        logger.warning(
            "company_fit.comment_too_long",
            extra={"actual_length": len(raw_comment.strip()), "max_length": FIT_COMMENT_MAX},
        )
        return None
    comment = _bounded_text(raw_comment, FIT_COMMENT_MAX)
    matched = raw.get("matched_focus")
    gaps = raw.get("gaps")
    if comment is None or not isinstance(matched, list) or not isinstance(gaps, list):
        logger.warning("company_fit.malformed_result")
        return None
    return CompanyFitPayload(
        comment=comment,
        matched_focus=[
            CompanyFitItem.model_validate(item) for item in _clean_items(matched, "evidence", company, question_ids)
        ],
        gaps=[CompanyFitGap.model_validate(item) for item in _clean_items(gaps, "suggestion", company, question_ids)],
    )


def _clean_items(
    entries: list[Any],
    text_key: str,
    company: CompanyContext,
    question_ids: set[str],
) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        text = _bounded_text(entry.get(text_key), FIT_ITEM_TEXT_MAX)
        fact_id = entry.get("fact_id")
        ids = entry.get("question_ids")
        if text is None or not isinstance(fact_id, str) or not isinstance(ids, list):
            continue
        if not all(isinstance(value, str) for value in ids):
            continue
        fact = company.fact_by_id(fact_id)
        if fact is None or fact_id in seen:
            continue
        seen.add(fact_id)
        cleaned.append(
            {
                "fact_id": fact.fact_id,
                "kind": fact.kind,
                "focus": fact.title,
                text_key: text,
                "question_ids": list(dict.fromkeys(value for value in ids if value in question_ids)),
            }
        )
    return cleaned[:FIT_ITEMS_MAX]


def _bounded_text(value: object, max_chars: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if len(text) > max_chars:
        return None
    return text or None
