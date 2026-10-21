"""운영 competency-v1 파서와 계산식을 사용하는 오프라인 실행 어댑터."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.core.common.feedback.competency.grading import parse_submission
from app.core.common.feedback.competency.mapping import CompetencyTargets, normalize_category, resolve_targets
from app.core.common.feedback.competency.prompt import add_target_guidance
from app.core.common.feedback.competency.scoring import summarize_session
from app.core.common.feedback.competency.tools import feedback_tool
from app.core.common.feedback.depth import evidence_sources
from app.core.common.feedback.solo.dto import AssembledSession


def targets_for_case(case: dict[str, Any], assembled: AssembledSession) -> dict[str, CompetencyTargets]:
    roles = {p["id"]: p["role"] for p in case.get("personas", [])}
    questions = {q["id"]: q for q in case["questions"]}
    targets = {}
    for t in assembled.targets:
        q = questions[t.question_id]
        category = normalize_category(q.get("category"))
        parent = questions.get(q.get("parent_id"))
        if category is None and q["type"] == "FOLLOW" and parent:
            category = normalize_category(parent.get("category"))
        role = q.get("persona_role", roles.get(q.get("persona_id"), case.get("persona_role")))
        targets[t.question_id] = resolve_targets(category, case.get("job_role"), role)
    return targets


def grading_input(case: dict[str, Any], assembled: AssembledSession, message: str) -> tuple[str, dict[str, Any]]:
    return add_target_guidance(message, targets_for_case(case, assembled)), feedback_tool(
        case.get("job_role"), multi=case["mode"] == "multi"
    )


def parse_result(case: dict[str, Any], assembled: AssembledSession, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    roles = {p["id"]: p["role"] for p in case.get("personas", [])}
    targets = targets_for_case(case, assembled)
    parsed = parse_submission(
        blocks,
        targets,
        {
            t.question_id: evidence_sources(t.answer, t.parent_answer if t.type == "FOLLOW" else None, 3000)
            for t in assembled.targets
        },
        persona_ids=tuple(p["id"] for p in case.get("personas", [])),
        non_tech_ids=frozenset(
            q["id"]
            for q in case["questions"]
            if roles.get(q.get("persona_id"), q.get("persona_role", case.get("persona_role", "TECH"))).upper() != "TECH"
        ),
        issue_sources={t.question_id: (t.answer[:3000],) for t in assembled.targets},
    )
    for entry in parsed["feedbacks"].values():
        for key in ("model_answer", "comment"):
            if not isinstance(entry.get(key), str) or not entry[key].strip():
                raise ValueError(f"문항 텍스트 누락: {key}")
        for key in ("strengths", "improvements"):
            if not isinstance(entry.get(key), list) or not all(isinstance(value, str) for value in entry[key]):
                raise ValueError(f"문항 텍스트 형식 오류: {key}")
    questions = [parsed["feedbacks"][t.question_id]["axis_levels"] for t in assembled.targets]
    scores = summarize_session(questions, parsed["consistency"], parsed["consistency_issues"])
    levels = {
        qid: {**entry["axis_levels"].as_dict(), "response": entry["axis_levels"].response}
        for qid, entry in parsed["feedbacks"].items()
    }
    checks = {}
    for q in case["questions"]:
        for axis, expected in q.get("expected", {}).items():
            key = "response" if axis == "response" else axis.upper()
            checks[f"{q['id']}:{axis}"] = levels[q["id"]].get(key) == expected
    if "expected_issues" in case:
        checks["credibility_issues"] = len(parsed["consistency_issues"]) == case["expected_issues"]
    return {
        "total_score": scores.total_score,
        "axis_scores": scores.log_extra()["axis_scores"],
        "question_levels": levels,
        "consistency_score": scores.reliability_score,
        "overall": parsed["overall"],
        "feedbacks": list(parsed["feedbacks"].values()),
        "targets": {
            qid: {"primary": q.targets.primary, "source": q.targets.source}
            for qid, entry in parsed["feedbacks"].items()
            for q in [entry["axis_levels"]]
        },
        "expectation_checks": checks,
    }


@contextmanager
def capture_validation(record: dict[str, Any]) -> Iterator[None]:
    class Reasons(logging.Handler):
        def emit(self, event: logging.LogRecord) -> None:
            reason = getattr(event, "reason", None)
            if isinstance(reason, str):
                record["validation_failures"].append(reason)

    handler = Reasons()
    loggers = [logging.getLogger(f"app.core.common.feedback.{name}") for name in ("competency", "solo", "multi")]
    record.setdefault("validation_failures", [])
    for logger in loggers:
        logger.addHandler(handler)
    try:
        yield
    finally:
        for logger in loggers:
            logger.removeHandler(handler)
