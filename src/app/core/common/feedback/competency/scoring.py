from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.core.common.feedback.competency.catalog import LEVEL_POINTS, SCORING_VERSION, WEIGHTS, Competency, verdict
from app.core.common.feedback.competency.evidence import SLOTS
from app.core.common.feedback.competency.mapping import CompetencyTargets
from app.core.common.feedback.scoring import _round


@dataclass(frozen=True)
class QuestionScore:
    response: int
    targets: CompetencyTargets
    levels: dict[Competency, int]
    evidence: dict[Competency, dict[str, Any]]

    def as_dict(self) -> dict[str, int]:
        return {comp.value: level for comp, level in self.levels.items()}

    def feedback_fields(self) -> dict[str, Any]:
        return {
            "response_level": self.response,
            "axis_scores": {comp.value: level * LEVEL_POINTS for comp, level in self.levels.items() if comp in WEIGHTS},
            "competencies": [
                {
                    "competency": comp.value,
                    "role": "PRIMARY" if comp == self.targets.primary else "SECONDARY",
                    "level": level,
                    "score": level * LEVEL_POINTS,
                    "evidence": [
                        {"slot": slot, "quote": group[slot]}
                        for slot in SLOTS[comp]
                        if isinstance(group.get(slot), str) and group[slot].strip()
                    ],
                }
                for comp, level in self.levels.items()
                for group in [self.evidence.get(comp, {})]
            ],
        }


PARTIAL_RESPONSE = 2
HIT_RESPONSE = 3


def question_levels(
    response: int, targets: CompetencyTargets, groups: Mapping[Competency, int]
) -> dict[Competency, int]:
    if response == 0:
        return {targets.primary: 0} if targets.primary else {}
    result = {}
    if targets.primary:
        cap = 1 if response == 1 else 3 if response == PARTIAL_RESPONSE else 4
        result[targets.primary] = min(groups.get(targets.primary, 0), cap)
    if response >= PARTIAL_RESPONSE:
        for comp in targets.secondary:
            if groups.get(comp, 0) > 0:
                result[comp] = groups[comp]
    return result


@dataclass(frozen=True)
class CompetencySessionScores:
    total_score: int
    intent_alignment_score: int
    reliability_score: int | None
    payload: dict[str, Any]
    source_counts: dict[str, int]

    def breakdown_payload(self) -> dict[str, Any]:
        return self.payload

    def log_extra(self) -> dict[str, object]:
        return {
            "scoring_version": SCORING_VERSION,
            "axis_scores": {item["axis"]: item["score"] for item in self.payload["axes"]},
            "target_sources": self.source_counts,
        }


def _axis_summary(comp: Competency, questions: Sequence[QuestionScore]) -> dict[str, Any]:
    values = [q.levels[comp] for q in questions if comp in q.levels]
    mean = sum(values) / len(values) if values else None
    return {
        "score": _round(mean * LEVEL_POINTS) if mean is not None else None,
        "verdict": verdict(mean) if mean is not None else None,
        "evidence_count": len(values),
    }


def summarize_session(
    questions: Sequence[QuestionScore],
    consistency_level: int | None,
    issues: Sequence[dict[str, Any]] = (),
    *,
    persona: bool = False,
) -> CompetencySessionScores:
    axes = [
        {"axis": comp.value, "weight": weight if summary["score"] is not None else None, **summary}
        for comp, weight in WEIGHTS.items()
        for summary in [_axis_summary(comp, questions)]
    ]
    observed = [axis for axis in axes if axis["score"] is not None]
    total = (
        _round(sum(a["score"] * a["weight"] for a in observed) / sum(a["weight"] for a in observed)) if observed else 0
    )
    response = _round(sum(q.response for q in questions) * LEVEL_POINTS / len(questions)) if questions else 0
    consistency = (
        consistency_level * LEVEL_POINTS
        if len(questions) > 1 and consistency_level is not None and not persona
        else None
    )
    motivation = _axis_summary(Competency.MOTIVATION, questions)
    payload = {
        "scoring_version": SCORING_VERSION,
        "axes": axes,
        "consistency_score": consistency,
        "response": {
            "score": response,
            "hit_count": sum(q.response >= HIT_RESPONSE for q in questions),
            "graded_count": len(questions),
        },
        "motivation": motivation if motivation["score"] is not None else None,
        "credibility": None
        if persona
        else {"score": consistency, "issues": list(issues) if len(questions) > 1 else []},
    }
    return CompetencySessionScores(
        total, response, consistency, payload, dict(Counter(q.targets.source for q in questions))
    )
