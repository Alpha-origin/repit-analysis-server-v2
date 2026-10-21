from itertools import product

import pytest

from app.core.common.feedback.competency.catalog import (
    WEIGHTS,
    Competency,
    verdict,
)
from app.core.common.feedback.competency.mapping import CompetencyTargets
from app.core.common.feedback.competency.payload import CompetencyBreakdownPayload
from app.core.common.feedback.competency.scoring import QuestionScore, question_levels, summarize_session


def question(primary: Competency | None, levels: dict[Competency, int], response: int = 4) -> QuestionScore:
    return QuestionScore(response, CompetencyTargets(primary), levels, {})


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (0, {Competency.KNOWLEDGE: 0}),
        (1, {Competency.KNOWLEDGE: 1}),
        (2, {Competency.KNOWLEDGE: 3, Competency.OWNERSHIP: 4}),
        (3, {Competency.KNOWLEDGE: 4, Competency.OWNERSHIP: 4}),
        (4, {Competency.KNOWLEDGE: 4, Competency.OWNERSHIP: 4}),
    ],
)
def test_response_gate_and_unrequested_groups(response: int, expected: dict[Competency, int]) -> None:
    targets = CompetencyTargets(Competency.KNOWLEDGE, (Competency.OWNERSHIP, Competency.GROWTH))
    assert (
        question_levels(
            response,
            targets,
            {Competency.KNOWLEDGE: 4, Competency.OWNERSHIP: 4, Competency.GROWTH: 0, Competency.COLLABORATION: 4},
        )
        == expected
    )


def test_documented_three_question_example() -> None:
    questions = [
        question(Competency.KNOWLEDGE, {Competency.KNOWLEDGE: 3}),
        question(Competency.PROBLEM_SOLVING, {Competency.PROBLEM_SOLVING: 3, Competency.KNOWLEDGE: 4}),
        question(
            Competency.PROBLEM_SOLVING,
            {Competency.PROBLEM_SOLVING: 3, Competency.OWNERSHIP: 4, Competency.GROWTH: 2},
            2,
        ),
    ]
    scores = summarize_session(questions, 3)
    assert scores.total_score == 83
    assert [a["score"] for a in scores.payload["axes"]] == [75, 88, 100, None, 50]
    assert scores.payload["response"] == {"score": 83, "hit_count": 2, "graded_count": 3}
    assert scores.reliability_score == 75
    assert CompetencyBreakdownPayload.model_validate(scores.payload).scoring_version == "competency-v1"


def test_zero_is_observed_and_null_is_unobserved() -> None:
    scores = summarize_session([question(Competency.KNOWLEDGE, {Competency.KNOWLEDGE: 0}, 0)], 4)
    axes = scores.payload["axes"]
    assert axes[1] == {"axis": "KNOWLEDGE", "score": 0, "weight": 25, "verdict": "WEAK", "evidence_count": 1}
    assert axes[0] == {"axis": "PROBLEM_SOLVING", "score": None, "weight": None, "verdict": None, "evidence_count": 0}
    assert scores.reliability_score is None
    assert scores.total_score == 0
    assert all(a["score"] is None for a in summarize_session([question(None, {}, 0)], 4).payload["axes"])


def test_motivation_excluded_and_persona_credibility_is_null() -> None:
    questions = [
        question(Competency.KNOWLEDGE, {Competency.KNOWLEDGE: 4}),
        question(Competency.MOTIVATION, {Competency.MOTIVATION: 0}),
    ]
    scores = summarize_session(questions, 0, [{"kind": "ROLE"}])
    assert scores.total_score == 100
    assert scores.payload["motivation"]["score"] == 0
    assert scores.reliability_score == 0
    assert summarize_session(questions, 4, persona=True).payload["credibility"] is None


def test_displayed_axis_scores_always_reproduce_total() -> None:
    for levels in product(range(5), repeat=3):
        questions = [question(comp, {comp: level}) for comp, level in zip(list(WEIGHTS)[:3], levels, strict=True)]
        result = summarize_session(questions, None)
        axes = [a for a in result.payload["axes"] if a["score"] is not None]
        expected = int(sum(a["score"] * a["weight"] for a in axes) / sum(a["weight"] for a in axes) + 0.5)
        assert result.total_score == expected


@pytest.mark.parametrize(("mean", "expected"), [(1.49, "WEAK"), (1.5, "BORDERLINE"), (2.5, "SOLID"), (3.5, "STRONG")])
def test_verdict_boundaries(mean: float, expected: str) -> None:
    assert verdict(mean) == expected
