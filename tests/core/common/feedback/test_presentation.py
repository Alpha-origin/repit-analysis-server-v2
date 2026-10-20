from copy import deepcopy

import pytest

from app.core.common.feedback.presentation import normalize_follow_up_question, normalize_generated_fields


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        (" \n", None),
        (3, None),
        ({"private": "text"}, None),
        ("가" * 201, None),
        ("  질문? \n", "질문?"),
        ("가" * 200, "가" * 200),
        ("😀" * 200, "😀" * 200),
    ],
)
def test_optional_follow_up_is_bounded_without_truncation(value: object, expected: str | None) -> None:
    assert normalize_follow_up_question(value) == expected


def test_references_use_callback_order_without_touching_other_fields() -> None:
    entry = {
        "question_id": "62",
        "user_answer": "Q62 답변",
        "summary": "Q63과 문항 62를 비교",
        "strengths": ["Q62 근거", "SQ62와 Q63a는 제품명"],
        "depth_evidence": {"reason": "Q62 원문 인용"},
    }
    original = deepcopy(entry)
    result = normalize_generated_fields(entry, ("summary", "strengths"), ("63", "62"))
    assert result["summary"] == "Q1과 문항 2를 비교"
    assert result["strengths"] == ["Q2 근거", "SQ62와 Q63a는 제품명"]
    assert result["question_id"] == "62"
    assert result["user_answer"] == "Q62 답변"
    assert result["depth_evidence"] == original["depth_evidence"]
    assert entry == original


def test_ambiguous_unknown_and_duplicate_references_are_preserved() -> None:
    fields = ("comment",)
    entry = {"comment": "Q1 Q2 Q62 Q999"}
    assert normalize_generated_fields(entry, fields, ("2", "62"))["comment"] == "Q1 Q2 Q2 Q999"
    assert normalize_generated_fields(entry, fields, ("62", "62"))["comment"] == entry["comment"]


def test_opaque_follow_ids_do_not_break_reference_mapping() -> None:
    entry = {"comment": "Q62와 Q1", "improvements": ["문항 66 보완", None]}
    result = normalize_generated_fields(entry, ("comment", "improvements"), ("62", "follow-62", "66"))
    assert result == {"comment": "Q1와 Q1", "improvements": ["문항 3 보완", None]}
