import pytest
from tests.competency_helpers import group

from app.core.common.feedback.competency.catalog import Competency
from app.core.common.feedback.competency.evidence import check_group

CASES = [
    (Competency.KNOWLEDGE, {}, 0),
    (Competency.KNOWLEDGE, {"term_only": "근거"}, 1),
    (Competency.KNOWLEDGE, {"components": "근거"}, 2),
    (Competency.KNOWLEDGE, {"mechanism": "근거"}, 3),
    (Competency.KNOWLEDGE, {"mechanism": "근거", "condition": "근거"}, 4),
    (Competency.PROBLEM_SOLVING, {}, 0),
    (Competency.PROBLEM_SOLVING, {"generic_reason": "근거"}, 1),
    (Competency.PROBLEM_SOLVING, {"reason": "근거"}, 2),
    (Competency.PROBLEM_SOLVING, {"reason": "근거", "alternatives": "근거"}, 3),
    (Competency.PROBLEM_SOLVING, {"reason": "근거", "limitation": "근거"}, 3),
    (Competency.PROBLEM_SOLVING, {"reason": "근거", "alternatives": "근거", "limitation": "근거"}, 4),
    (Competency.OWNERSHIP, {}, 0),
    (Competency.OWNERSHIP, {"stance": "근거"}, 1),
    (Competency.OWNERSHIP, {"team_only": "근거", "method": "근거", "result": "근거"}, 2),
    (Competency.OWNERSHIP, {"action": "근거"}, 2),
    (Competency.OWNERSHIP, {"action": "근거", "method": "근거"}, 3),
    (Competency.OWNERSHIP, {"action": "근거", "method": "근거", "result": "근거"}, 4),
    (Competency.COLLABORATION, {}, 0),
    (Competency.COLLABORATION, {"generic": "근거"}, 1),
    (Competency.COLLABORATION, {"counterpart": "근거", "issue": "근거"}, 2),
    (Competency.COLLABORATION, {"coordination": "근거"}, 2),
    (Competency.COLLABORATION, {"counterpart": "근거", "issue": "근거", "coordination": "근거"}, 3),
    (Competency.COLLABORATION, {"counterpart": "근거", "issue": "근거", "coordination": "근거", "outcome": "근거"}, 4),
    (Competency.GROWTH, {}, 0),
    (Competency.GROWTH, {"generic": "근거"}, 1),
    (Competency.GROWTH, {"lesson": "근거"}, 2),
    (Competency.GROWTH, {"change": "근거"}, 2),
    (Competency.GROWTH, {"admission": "근거"}, 2),
    (Competency.GROWTH, {"lesson": "근거", "admission": "근거"}, 3),
    (Competency.GROWTH, {"lesson": "근거", "change": "근거"}, 3),
    (Competency.GROWTH, {"lesson": "근거", "change": "근거", "admission": "근거"}, 4),
    (Competency.MOTIVATION, {}, 0),
    (Competency.MOTIVATION, {"generic": "근거"}, 1),
    (Competency.MOTIVATION, {"company_fact": "근거"}, 2),
    (Competency.MOTIVATION, {"personal_link": "근거"}, 2),
    (Competency.MOTIVATION, {"company_fact": "근거", "personal_link": "근거"}, 4),
]


@pytest.mark.parametrize(("comp", "quotes", "expected"), CASES)
def test_slot_level_boundaries(comp: Competency, quotes: dict[str, str], expected: int) -> None:
    assert check_group(comp, group(comp, **quotes), ["근거"]) == (expected, None)


@pytest.mark.parametrize("accuracy", [None, 0, 1, 2, 3, 4])
def test_knowledge_accuracy_cap(accuracy: int | None) -> None:
    assert check_group(
        Competency.KNOWLEDGE,
        group(Competency.KNOWLEDGE, mechanism="근거", condition="근거", accuracy=accuracy),
        ["근거"],
    )[0] == (4 if accuracy is None else accuracy)


def test_invalid_accuracy_and_made_up_quote_are_rejected() -> None:
    assert (
        check_group(Competency.KNOWLEDGE, group(Competency.KNOWLEDGE, accuracy=True), ["근거"])[1] == "invalid_accuracy"
    )
    assert (
        check_group(Competency.GROWTH, group(Competency.GROWTH, lesson="지어낸 원문"), ["근거"])[1]
        == "evidence_mismatch:growth.lesson"
    )
