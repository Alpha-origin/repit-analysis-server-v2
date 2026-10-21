from collections import Counter
from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock

import pytest
from evaluation.cases import DEFAULT_CASES, load_cases
from evaluation.competency_metrics import correlation, inter_rater_pairs, stability, weighted_kappa
from evaluation.competency_run import (
    grading_input as competency_input,
    parse_result,
    targets_for_case,
)
from evaluation.feedback_attempts import evaluate
from evaluation.report_competency import render_report
from evaluation.run_feedback import (
    assembled_case,
    grading_input,
    parse_result as parse_submission,
)

from app.core.common.feedback.competency.catalog import Competency
from app.core.common.feedback.grading_retry import IncompleteGradingError
from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicCallResult
from tests.competency_helpers import output
from tests.multi_helpers import StubTextClient


def case() -> dict[str, Any]:
    return {
        "id": "sample",
        "mode": "solo",
        "split": "development",
        "questions": [
            {
                "id": "q-1",
                "type": "ORIGINAL",
                "category": "tech_choice",
                "content": "왜 선택했나요",
                "intention": "이유 확인",
                "answer": "성능 요구 때문에 선택했습니다",
                "expected": {"problem_solving": 2},
            }
        ],
    }


def run_row(repeat: int = 1, grade: int = 2) -> dict[str, Any]:
    return {
        "case_id": "sample",
        "variant": "current",
        "repeat": repeat,
        "scoring_version": "competency-v1",
        "status": "succeeded",
        "duration_seconds": 1,
        "total_score": grade * 25,
        "axis_scores": {"PROBLEM_SOLVING": grade * 25},
        "question_levels": {"q-1": {"PROBLEM_SOLVING": grade}},
    }


def test_kappa_and_correlation_are_not_fabricated_for_empty_or_constant_data() -> None:
    assert weighted_kappa([]) is None
    assert weighted_kappa([(4, 4)]) is None
    assert weighted_kappa([(0, 0), (4, 4)]) == 1
    assert weighted_kappa([(0, 4), (4, 0)]) == -1
    assert correlation([(1, 2), (2, 4), (3, 6)]) == 1
    assert correlation([(1, 2), (1, 3)]) is None


def test_stability_groups_by_case_and_does_not_treat_missing_as_zero() -> None:
    assert stability([run_row()]) == (None, None)
    assert stability([run_row(1), run_row(2)]) == (1, 0)


def test_human_duplicate_ratings_rejected() -> None:
    rating = {"case_id": "sample", "question_id": "q-1", "reviewer_id": "r", "knowledge": "2"}
    with pytest.raises(ValueError, match="중복"):
        inter_rater_pairs([rating, rating], "knowledge")


def test_no_human_data_cannot_be_reported_as_passing() -> None:
    report = render_report([run_row()], [])
    assert "운영 전환 보류" in report
    assert "사람 평가자 0명" in report
    assert "미측정" in report
    assert "실제 사람 평가와 다름" in report


def test_category_and_expected_values_use_production_parser() -> None:
    example = case()
    assembled = assembled_case(example)
    assert targets_for_case(example, assembled)["q-1"].primary == Competency.PROBLEM_SOLVING
    raw = output(reason="성능 요구 때문에 선택했습니다")
    blocks = [{"type": "tool_use", "name": "submit_feedback", "input": raw}]
    result = parse_result(example, assembled, blocks)
    assert result["total_score"] == 50
    assert result["expectation_checks"] == {"q-1:problem_solving": True}


def test_case_counts_cover_all_validation_tags_and_three_general_jobs() -> None:
    cases = load_cases(DEFAULT_CASES, "all")
    assert len(cases) >= 40
    counts = Counter(tag for example in cases for tag in example["tags"])
    minima = {
        "implementation-no-reason": 3,
        "team-only": 3,
        "subject-omitted": 2,
        "honest-unknown": 3,
        "confident-wrong": 3,
        "collaboration": 4,
        "growth": 4,
        "motivation": 3,
        "follow-contradiction": 2,
        "follow-weak": 2,
        "legacy-category": 2,
        "general-job": 6,
    }
    assert all(counts[tag] >= minimum for tag, minimum in minima.items())
    assert len({c["job_role"] for c in cases if "general-job" in c["tags"] and c["job_role"].startswith("GEN_")}) >= 3


async def test_offline_attempts_match_missing_primary_retry_policy() -> None:
    example = case()
    assembled = assembled_case(example)
    raw = output()
    raw["feedbacks"][0]["evidence"]["problem_solving"] = None
    client = StubTextClient({"submit_feedback": raw})
    message, _ = grading_input(example, assembled)
    message, tool = competency_input(example, assembled, message)
    record = {"model": "stub", "scoring_version": "competency-v1", "max_tokens": 12288}
    with pytest.raises(IncompleteGradingError, match="일부 문항"):
        await evaluate(client, example, assembled, record, "system", message, tool, parse_submission)
    assert record["attempts"] == 2
    assert record["input_tokens"] == 20
    assert record["validation_failures"] == ["missing_primary_group", "missing_primary_group"]


@pytest.mark.parametrize("mode", ["solo", "multi"])
@pytest.mark.parametrize("outcome", ["recovered", "exhausted", "truncated"])
async def test_missing_question_uses_production_retry_policy(mode: str, outcome: str) -> None:
    example = case()
    example["mode"] = mode
    example["questions"].append({**example["questions"][0], "id": "q-2"})
    name = "submit_feedback"
    complete = output(reason="성능 요구 때문에 선택했습니다")
    complete["feedbacks"].append({**complete["feedbacks"][0], "question_id": "q-2"})
    if mode == "multi":
        name = "submit_multi_feedback"
        example["personas"] = [{"id": "p-tech", "role": "TECH"}, {"id": "p-hr", "role": "HR"}]
        example["questions"][0]["persona_id"] = "p-tech"
        example["questions"][1].update({"persona_id": "p-hr", "category": "judgment"})
        complete["personas"] = [{"persona_id": "p-tech", "comment": "평가"}, {"persona_id": "p-hr", "comment": "평가"}]
    incomplete = deepcopy(complete)
    incomplete["feedbacks"].pop()
    first = AnthropicCallResult(
        content_blocks=[{"type": "tool_use", "name": name, "input": incomplete}],
        input_tokens=10,
        output_tokens=10,
        stop_reason="max_tokens" if outcome == "truncated" else "tool_use",
    )
    second = (
        first.model_copy(update={"content_blocks": [{"type": "tool_use", "name": name, "input": complete}]})
        if outcome == "recovered"
        else first
    )
    client = StubTextClient({})
    spy = AsyncMock(side_effect=[first, second])
    client.call = spy  # type: ignore[method-assign]
    assembled = assembled_case(example)
    message, _ = grading_input(example, assembled)
    message, tool = competency_input(example, assembled, message)
    record: dict[str, Any] = {"model": "stub", "scoring_version": "competency-v1", "max_tokens": 14336}

    if outcome == "recovered":
        result = await evaluate(client, example, assembled, record, "system", message, tool, parse_submission)
        assert set(result["question_levels"]) == {"q-1", "q-2"}
        assert result["total_score"] == 50
    else:
        with pytest.raises(IncompleteGradingError):
            await evaluate(client, example, assembled, record, "system", message, tool, parse_submission)

    expected_attempts = 1 if outcome == "truncated" else 2
    assert spy.await_count == expected_attempts
    assert record["attempts"] == expected_attempts
    assert record["input_tokens"] == 10 * expected_attempts
    assert record["output_tokens"] == 10 * expected_attempts
