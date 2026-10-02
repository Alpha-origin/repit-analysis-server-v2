from typing import Any

import pytest

from app.core.common.applicant_profile.evidence import available_categories, evidence_paths
from app.core.common.question_cycle.dto import CycleMode
from app.core.common.question_cycle.rules import find_violations, parse_questions, sort_questions
from tests.cycle_helpers import entry, multi_entries, refined_profile, solo_entries


def _check(entries: list[dict[str, Any]], mode: CycleMode, drop: tuple[str, ...] = ()) -> list[str]:
    profile = refined_profile(drop)
    questions, violations = parse_questions({"questions": entries}, 400)
    return violations + find_violations(questions, mode, available_categories(profile), evidence_paths(profile))


def test_valid_solo_cycle_passes() -> None:
    assert _check(solo_entries(), "SOLO") == []


def test_valid_multi_cycle_passes() -> None:
    assert _check(multi_entries(), "MULTI") == []


def test_solo_with_missing_material_fills_slots_with_available_categories() -> None:
    drop = ("troubleshootings", "integrations")
    available = ("tech_choice", "implementation", "structure")

    assert _check(solo_entries(available), "SOLO", drop) == []


def test_unavailable_category_is_a_violation() -> None:
    drop = ("troubleshootings",)
    violations = _check(solo_entries(), "SOLO", drop)

    assert any("재료가 없는 카테고리" in v and "troubleshooting" in v for v in violations)


def test_solo_set_missing_a_category_is_a_violation() -> None:
    entries = solo_entries()
    target = next(e for e in entries if e["set_no"] == 2 and e["category"] == "integration")
    target["category"] = "implementation"

    violations = _check(entries, "SOLO")

    assert any("세트 2에 빠진 카테고리: integration" in v for v in violations)


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda es: es.pop(), "정확히 15개"),
        (lambda es: es[0].update(set_no=4), "setNo 는 1~3"),
        (lambda es: es[0].update(based_on=["order-api/src/Unknown.java"]), "목록에 없는 경로"),
        (lambda es: es[0].update(based_on=["file_tree"]), "목록에 없는 경로"),
        (lambda es: es[0].update(intention=" "), "비어 있으면 안 된다"),
        (lambda es: es[0].update(based_on=[]), "based_on 이 비어 있다"),
        (lambda es: es[0].update(category="design"), "category 가"),
    ],
)
def test_solo_rule_violations(change: Any, expected: str) -> None:
    entries = solo_entries()
    change(entries)

    violations = _check(entries, "SOLO")

    assert any(expected in v for v in violations), violations


def test_multi_duplicate_category_in_set_is_a_violation() -> None:
    entries = multi_entries()
    entries[1]["category"] = "tech_choice"
    entries[1]["based_on"] = ["order-api/build.gradle"]

    violations = _check(entries, "MULTI")

    assert any("세트 1 안에서 카테고리가 겹친다" in v for v in violations)


def test_multi_cycle_must_cover_every_available_category() -> None:
    entries = [entry(set_no, category) for set_no in (1, 2, 3) for category in ("tech_choice", "implementation")]

    violations = _check(entries, "MULTI")

    assert any("사이클 전체에 빠진 카테고리" in v for v in violations)


def test_long_expected_answer_is_truncated_not_rejected() -> None:
    entries = solo_entries()
    entries[0]["expected_answer"] = "가" * 500

    questions, violations = parse_questions({"questions": entries}, 400)

    assert violations == []
    assert len(questions[0].expected_answer) == 400


def test_sort_orders_by_set_then_category() -> None:
    questions, _ = parse_questions({"questions": solo_entries()}, 400)

    ordered = sort_questions(questions)

    assert [(q.set_no, q.category) for q in ordered[:5]] == [
        (1, "tech_choice"),
        (1, "implementation"),
        (1, "troubleshooting"),
        (1, "integration"),
        (1, "structure"),
    ]
    assert [q.set_no for q in ordered] == [1] * 5 + [2] * 5 + [3] * 5
