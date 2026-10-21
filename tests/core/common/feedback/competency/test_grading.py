from typing import Any

import pytest
from tests.competency_helpers import group, output

from app.core.common.feedback.competency.catalog import Competency
from app.core.common.feedback.competency.grading import parse_question, parse_submission, validated_issues
from app.core.common.feedback.competency.mapping import CompetencyTargets
from app.core.common.feedback.grading_retry import IncompleteGradingError

TARGET = CompetencyTargets(Competency.PROBLEM_SOLVING, (Competency.KNOWLEDGE,))


@pytest.mark.parametrize("response", [None, -1, 5, True, "4"])
def test_invalid_response_rejected(response: object) -> None:
    entry = output()["feedbacks"][0]
    entry["response"] = response
    assert parse_question(entry, TARGET, ["근거"]) is None


def test_primary_group_and_quote_are_required(caplog: pytest.LogCaptureFixture) -> None:
    entry = output()["feedbacks"][0]
    entry["evidence"]["problem_solving"] = None
    assert parse_question(entry, TARGET, ["근거"]) is None
    assert "missing_primary_group" in caplog.text
    entry["evidence"]["problem_solving"] = group(Competency.PROBLEM_SOLVING, reason="비밀 원문")
    assert parse_question(entry, TARGET, ["근거"]) is None
    assert "evidence_mismatch:problem_solving.reason" in caplog.text
    assert "비밀 원문" not in caplog.text


def test_invalid_secondary_drops_only_secondary() -> None:
    entry = output(reason="근거")["feedbacks"][0]
    entry["evidence"]["knowledge"] = group(Competency.KNOWLEDGE, mechanism="지어낸 인용")
    score = parse_question(entry, TARGET, ["근거"])
    assert score is not None
    assert score.levels == {Competency.PROBLEM_SOLVING: 2}
    entry["evidence"]["knowledge"] = group(Competency.KNOWLEDGE, accuracy=5)
    assert parse_question(entry, TARGET, ["근거"]) is not None


def test_invalid_primary_accuracy_rejected() -> None:
    entry = output(Competency.KNOWLEDGE, accuracy=5)["feedbacks"][0]
    assert parse_question(entry, CompetencyTargets(Competency.KNOWLEDGE), ["근거"]) is None


def test_unclassified_inference_and_no_inference() -> None:
    entry = output(reason="근거")["feedbacks"][0]
    entry["inferred_primary"] = "PROBLEM_SOLVING"
    score = parse_question(entry, CompetencyTargets(None), ["근거"])
    assert score is not None
    assert score.targets.source == "inferred"
    assert score.levels == {Competency.PROBLEM_SOLVING: 2}
    entry["inferred_primary"] = None
    score = parse_question(entry, CompetencyTargets(None), ["근거"])
    assert score is not None
    assert score.levels == {}


@pytest.mark.parametrize("multi", [False, True])
def test_missing_entries_raise_retryable_error(multi: bool) -> None:
    raw = output()
    raw["feedbacks"] = []
    blocks = [{"type": "tool_use", "name": "submit_multi_feedback" if multi else "submit_feedback", "input": raw}]
    with pytest.raises(IncompleteGradingError):
        parse_submission(blocks, {"q-1": TARGET}, {"q-1": ("근거",)}, persona_ids=("p-0",) if multi else ())


def test_only_valid_cross_question_contradictions_are_preserved(caplog: pytest.LogCaptureFixture) -> None:
    valid: dict[str, Any] = {"kind": "ROLE", "question_ids": ["a", "b"], "quotes": ["내가 전부", "동료가 전부"]}
    raw = [
        valid,
        {**valid, "quotes": ["개인 원문", "동료가 전부"]},
        {**valid, "kind": {}},
        {**valid, "question_ids": ["a", "a"]},
    ]
    assert validated_issues(raw, {"a": ("내가 전부",), "b": ("동료가 전부",)}) == [valid]
    assert "개인 원문" not in caplog.text
