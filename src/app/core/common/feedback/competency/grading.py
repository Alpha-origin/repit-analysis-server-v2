from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from app.core.common.feedback.competency.catalog import Competency
from app.core.common.feedback.competency.evidence import check_group
from app.core.common.feedback.competency.mapping import CompetencyTargets, infer_targets
from app.core.common.feedback.competency.scoring import QuestionScore, question_levels
from app.core.common.feedback.grading_retry import IncompleteGradingError, log_dropped, unknown_key
from app.core.common.feedback.question_label import (
    FEEDBACK_TEXT_FIELDS,
    OVERALL_TEXT_FIELDS,
    PERSONA_TEXT_FIELDS,
    field_texts,
    warn_out_of_range_labels,
)
from app.core.common.feedback.quotes import quote_present
from app.core.common.feedback.scoring import _level, parse_consistency
from app.core.common.interview_qa.errors import PipelineError
from app.core.common.tool_use import extract_tool_input

logger = logging.getLogger(__name__)
ISSUE_PAIR_SIZE = 2


def parse_question(
    entry: dict[str, Any],
    targets: CompetencyTargets,
    sources: Sequence[str],
    *,
    non_tech: bool = False,
    attempt: int = 1,
) -> QuestionScore | None:
    qid = entry["question_id"]
    response = _level(entry.get("response"))
    if response is None:
        log_dropped(logger, "feedback.competency.dropped", qid, "invalid_response", attempt)
        return None
    if targets.primary is None:
        targets = infer_targets(entry.get("inferred_primary"), non_tech=non_tech)
        if targets.primary is None:
            log_dropped(logger, "feedback.competency.dropped", qid, "no_inferred_primary", attempt)
    elif entry.get("inferred_primary") is not None:
        log_dropped(logger, "feedback.competency.dropped", qid, "unexpected_inferred_primary", attempt)
    evidence = entry.get("evidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    levels: dict[Competency, int] = {}
    groups: dict[Competency, dict[str, Any]] = {}
    desired = ((targets.primary,) if targets.primary else ()) + targets.secondary
    for comp in desired:
        raw = evidence.get(comp.value.lower())
        if raw is None and comp != targets.primary:
            continue
        level, reason = check_group(comp, raw, sources)
        if level is None:
            reason = "missing_primary_group" if raw is None else reason
            reason = reason if comp == targets.primary else f"secondary_dropped:{comp.value.lower()}"
            log_dropped(logger, "feedback.competency.dropped", qid, reason or "evidence_format", attempt)
            if comp == targets.primary:
                return None
            continue
        levels[comp] = level
        if isinstance(raw, dict):
            groups[comp] = raw
    return QuestionScore(response, targets, question_levels(response, targets, levels), groups)


def validated_issues(raw: object, sources: Mapping[str, tuple[str, ...]]) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    issues = []
    for entry in raw:
        if not isinstance(entry, dict):
            log_dropped(logger, "feedback.competency.dropped", "issue", "issue_dropped", 1)
            continue
        ids, quotes = entry.get("question_ids"), entry.get("quotes")
        valid = (
            isinstance(entry.get("kind"), str)
            and entry["kind"] in {"ROLE", "TECH_CHOICE", "CAUSE", "DETAIL", "OTHER"}
            and isinstance(ids, list)
            and len(ids) == ISSUE_PAIR_SIZE
            and all(isinstance(qid, str) and qid in sources for qid in ids)
            and ids[0] != ids[1]
            and isinstance(quotes, list)
            and len(quotes) == ISSUE_PAIR_SIZE
            and all(
                isinstance(quote, str) and quote_present(quote, sources[qid])
                for qid, quote in zip(ids, quotes, strict=True)
            )
        )
        if valid:
            issues.append({"kind": entry["kind"], "question_ids": ids, "quotes": quotes})
        else:
            log_dropped(logger, "feedback.competency.dropped", "issue", "issue_dropped", 1)
    return issues


def parse_submission(
    blocks: list[dict[str, Any]],
    targets: Mapping[str, CompetencyTargets],
    sources: Mapping[str, tuple[str, ...]],
    *,
    persona_ids: tuple[str, ...] = (),
    non_tech_ids: frozenset[str] = frozenset(),
    attempt: int = 1,
    issue_sources: Mapping[str, tuple[str, ...]] | None = None,
) -> dict[str, Any]:
    multi = bool(persona_ids)
    raw = extract_tool_input(blocks, "submit_multi_feedback" if multi else "submit_feedback")
    if raw is None or not isinstance(raw.get("overall"), dict) or not isinstance(raw.get("feedbacks"), list):
        raise PipelineError(500, "피드백 생성 결과가 형식을 충족하지 못했습니다.")
    by_id = {}
    for entry in raw["feedbacks"]:
        if not isinstance(entry, dict):
            continue
        qid = entry.get("question_id")
        if not isinstance(qid, str) or qid not in targets:
            log_dropped(logger, "feedback.competency.dropped", unknown_key(qid), "unknown_question_id", attempt)
            continue
        score = parse_question(entry, targets[qid], sources[qid], non_tech=qid in non_tech_ids, attempt=attempt)
        if score is not None:
            by_id[qid] = {**entry, "axis_levels": score}
    personas = (
        {
            entry["persona_id"]: entry
            for entry in raw.get("personas", [])
            if isinstance(entry, dict)
            and isinstance(entry.get("persona_id"), str)
            and entry["persona_id"] in persona_ids
        }
        if isinstance(raw.get("personas", []), list)
        else {}
    )
    if any(qid not in by_id for qid in targets) or any(pid not in personas for pid in persona_ids):
        message = "일부 채점 결과가 생성되지 않았습니다." if multi else "일부 문항의 피드백이 생성되지 않았습니다."
        raise IncompleteGradingError(500, message)
    warn_out_of_range_labels(
        logger,
        "feedback.competency.question_id_label",
        field_texts([raw["overall"]], OVERALL_TEXT_FIELDS)
        + field_texts(by_id.values(), FEEDBACK_TEXT_FIELDS)
        + field_texts(personas.values(), PERSONA_TEXT_FIELDS),
        len(targets),
    )
    return {
        "overall": raw["overall"],
        "feedbacks": by_id,
        "personas": personas,
        "consistency": parse_consistency(raw["overall"].get("consistency")),
        "consistency_issues": validated_issues(raw["overall"].get("consistency_issues"), issue_sources or sources),
        "scoring_version": "competency-v1",
    }
