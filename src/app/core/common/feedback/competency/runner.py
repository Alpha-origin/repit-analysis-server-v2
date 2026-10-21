from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from app.core.common.feedback.competency.grading import parse_submission
from app.core.common.feedback.competency.mapping import resolve_targets
from app.core.common.feedback.competency.prompt import add_target_guidance, system_prompt
from app.core.common.feedback.competency.tools import feedback_tool
from app.core.common.feedback.depth import evidence_sources
from app.core.common.feedback.grading_retry import GRADING_ATTEMPTS, IncompleteGradingError
from app.core.common.feedback.multi.dto import FeedbackPersona
from app.core.common.feedback.multi.prompt import build_grading_user_message as multi_message
from app.core.common.feedback.solo.dto import AssembledSession
from app.core.common.feedback.solo.prompt import build_grading_user_message as solo_message
from app.core.common.interview_qa.errors import PipelineError
from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicTextClient, AnthropicTextClientError

logger = logging.getLogger(__name__)


async def grade(
    client: AnthropicTextClient,
    model: str,
    max_tokens: int,
    answer_max_chars: int,
    assembled: AssembledSession,
    *,
    job_role: str | None,
    job_title: str | None,
    persona_type: str | None = None,
    persona_tone: str | None = None,
    personas: Sequence[FeedbackPersona] = (),
    persona_by_question: Mapping[str, FeedbackPersona] | None = None,
) -> dict[str, Any]:
    owners = persona_by_question or {}
    roles = {qid: p.role for qid, p in owners.items()}
    targets = {
        target.question_id: resolve_targets(target.category, job_role, roles.get(target.question_id))
        for target in assembled.targets
    }
    sources = {
        t.question_id: evidence_sources(
            t.answer,
            t.parent_answer if t.type == "FOLLOW" and t.parent_question is not None else None,
            answer_max_chars,
        )
        for t in assembled.targets
    }
    issue_sources = {t.question_id: (t.answer[:answer_max_chars],) for t in assembled.targets}
    message = (
        multi_message(assembled, personas, owners, answer_max_chars, job_title=job_title)
        if personas
        else solo_message(assembled, persona_type, answer_max_chars, persona_tone=persona_tone, job_title=job_title)
    )
    message = add_target_guidance(message, targets)
    event = "feedback_multi" if personas else "feedback_solo"
    for attempt in range(1, GRADING_ATTEMPTS + 1):
        try:
            response = await client.call(
                model=model,
                system=system_prompt(job_role, multi=bool(personas)),
                messages=[{"role": "user", "content": message}],
                tools=[feedback_tool(job_role, multi=bool(personas))],
                tool_choice={"type": "auto"},
                max_tokens=max_tokens,
            )
        except AnthropicTextClientError as exc:
            raise PipelineError(502, "피드백 생성에 실패했습니다. 잠시 후 다시 시도해 주세요.") from exc
        if response.stop_reason == "max_tokens":
            logger.warning(
                "%s.grading.truncated", event, extra={"max_tokens": max_tokens, "scoring_version": "competency-v1"}
            )
        try:
            parsed = parse_submission(
                response.content_blocks,
                targets,
                sources,
                persona_ids=tuple(p.persona_id for p in personas),
                non_tech_ids=frozenset(qid for qid, role in roles.items() if role.strip().upper() != "TECH"),
                attempt=attempt,
                issue_sources=issue_sources,
            )
            counts = dict(Counter(entry["axis_levels"].targets.source for entry in parsed["feedbacks"].values()))
            logger.info("feedback.competency.targets sources=%s", counts, extra={"target_sources": counts})
            parsed["usage"] = {
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "attempts": attempt,
            }
            return parsed
        except IncompleteGradingError:
            if attempt == GRADING_ATTEMPTS or response.stop_reason == "max_tokens":
                raise
            logger.warning("%s.grading.retry attempt=%d", event, attempt, extra={"attempt": attempt})
    raise AssertionError("unreachable")
