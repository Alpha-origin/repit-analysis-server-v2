from __future__ import annotations

import asyncio
import logging
import math
import time
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
from app.core.common.feedback.question_label import warn_out_of_range_labels
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
        timeout_seconds: float = 20,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("company fit timeout must be positive and finite")
        self._timeout_seconds = timeout_seconds
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
        started = time.monotonic()
        try:
            # SDK 내부 대기·재시도를 포함해 fit 시작부터 전체 논리 호출을 제한한다.
            async with asyncio.timeout(self._timeout_seconds):
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
        except TimeoutError:
            logger.warning("company_fit.timeout", extra={"elapsed_ms": (time.monotonic() - started) * 1000})
            return None
        tool_blocks = [block for block in response.content_blocks if block.get("type") == "tool_use"]
        raw = extract_tool_input(response.content_blocks, TOOL_NAME)
        result = None
        if raw is None or response.stop_reason != "tool_use" or len(tool_blocks) != 1:
            logger.warning("company_fit.submission_missing_or_truncated")
        else:
            result = _parse_submission(raw, company, {target.question_id for target in targets})
        if result is not None:
            # 문장에 블록 번호(Q1…) 대신 question_id 를 쓴 흔적을 찾는다. 로그만 남기고 결과는 그대로 쓴다.
            # 블록 번호는 targets 순서로 매기므로 대상 수도 targets 기준이다.
            warn_out_of_range_labels(
                logger,
                "company_fit.question_id_label",
                [
                    result.comment,
                    *(item.evidence for item in result.matched_focus),
                    *(gap.suggestion for gap in result.gaps),
                ],
                len(targets),
            )
        logger.info(
            "company_fit.completed",
            extra={
                "applied": result is not None,
                "matched_count": len(result.matched_focus) if result else 0,
                "gap_count": len(result.gaps) if result else 0,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "elapsed_ms": (time.monotonic() - started) * 1000,
            },
        )
        return result


def _parse_submission(
    raw: dict[str, Any],
    company: CompanyContext,
    question_ids: set[str],
) -> CompanyFitPayload | None:
    if set(raw) != {"comment", "matched_focus", "gaps"}:
        logger.warning("company_fit.unexpected_fields")
        return None
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
    matched_items = _clean_items(matched, "evidence", company, question_ids)
    gap_items = _clean_items(gaps, "suggestion", company, question_ids)
    if (matched or gaps) and not (matched_items or gap_items):
        logger.warning("company_fit.no_valid_evidence")
        return None
    return CompanyFitPayload(
        comment=comment,
        matched_focus=[CompanyFitItem.model_validate(item) for item in matched_items],
        gaps=[CompanyFitGap.model_validate(item) for item in gap_items],
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
        if not isinstance(entry, dict) or set(entry) != {"fact_id", text_key, "question_ids"}:
            continue
        text = _bounded_text(entry.get(text_key), FIT_ITEM_TEXT_MAX)
        fact_id = entry.get("fact_id")
        ids = entry.get("question_ids")
        if text is None or not isinstance(fact_id, str) or not isinstance(ids, list):
            continue
        if not all(isinstance(value, str) for value in ids):
            continue
        fact = next((fact for fact in company.facts if fact.prompt_id == fact_id), None)
        if fact is None or fact.fact_id in seen:
            continue
        valid_ids = list(dict.fromkeys(value for value in ids if value in question_ids))
        if not valid_ids:
            continue
        seen.add(fact.fact_id)
        cleaned.append(
            {
                "fact_id": fact.fact_id,
                "kind": fact.kind,
                "focus": fact.title,
                text_key: text,
                "question_ids": valid_ids,
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
