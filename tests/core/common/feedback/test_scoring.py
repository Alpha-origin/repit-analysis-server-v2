import pytest

from app.core.common.feedback.scoring import (
    AxisLevels,
    compute_breakdown,
    parse_axis_levels,
    parse_consistency,
    summarize_session,
)


def test_total_is_weighted_sum_of_rounded_axis_scores() -> None:
    breakdown = compute_breakdown([AxisLevels(4, 2, 3, 4), AxisLevels(3, 1, 2, 4)])

    assert breakdown is not None
    # 축 점수: 의도 (100+75)/2=87.5→88, 깊이 37.5→38, 구체성 62.5→63, 정확성 100
    assert breakdown.axis_scores() == {"intent": 88, "depth": 38, "specificity": 63, "accuracy": 100}
    # 표시된 정수로 계산해야 사용자가 직접 계산한 값과 맞는다: (88x35 + 38x25 + 63x25 + 100x15) / 100 = 71.05
    assert breakdown.total_score == 71


def test_rounding_is_half_up_not_bankers() -> None:
    # 의도 2단계(50)와 3단계(75) 평균 62.5 — 파이썬 round 라면 62 가 된다.
    breakdown = compute_breakdown([AxisLevels(2, 2, 2, 2), AxisLevels(3, 3, 3, 3)])

    assert breakdown is not None
    assert breakdown.intent == 63


def test_accuracy_null_is_excluded_per_question_and_reweighted_when_absent() -> None:
    partial = compute_breakdown([AxisLevels(4, 4, 4, None), AxisLevels(4, 4, 4, 0)])
    absent = compute_breakdown([AxisLevels(4, 2, 2, None)])

    assert partial is not None
    assert partial.accuracy == 0  # null 인 문항은 평균에서 빠진다
    assert absent is not None
    assert absent.accuracy is None
    assert absent.weights == {"intent": 35, "depth": 25, "specificity": 25}
    # (100x35 + 50x25 + 50x25) / 85 = 70.6
    assert absent.total_score == 71


def test_no_graded_questions_yields_none() -> None:
    assert compute_breakdown([]) is None


def test_intent_zero_forces_other_axes_to_zero() -> None:
    assert parse_axis_levels({"intent": 0, "depth": 4, "specificity": 3, "accuracy": 4}) == AxisLevels(0, 0, 0, 0)
    assert parse_axis_levels({"intent": 0, "depth": 4, "specificity": 3, "accuracy": None}) == AxisLevels(0, 0, 0, None)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {"intent": 5, "depth": 3, "specificity": 2, "accuracy": 4},
        {"intent": 4, "depth": "3", "specificity": 2, "accuracy": 4},
        {"intent": True, "depth": 3, "specificity": 2, "accuracy": 4},
        {"intent": 4, "depth": 3, "specificity": 2, "accuracy": -1},
    ],
)
def test_invalid_levels_are_rejected(raw: object) -> None:
    assert parse_axis_levels(raw) is None


def test_missing_accuracy_is_treated_as_not_applicable() -> None:
    assert parse_axis_levels({"intent": 4, "depth": 3, "specificity": 2}) == AxisLevels(4, 3, 2, None)


def test_reliability_mixes_consistency_and_specificity() -> None:
    scores = summarize_session([AxisLevels(4, 4, 2, 4), AxisLevels(4, 4, 2, 4)], consistency_level=4)

    assert scores is not None
    assert scores.consistency == 100
    assert scores.reliability_score == 75  # (100 + 50) / 2


def test_consistency_is_dropped_for_single_answer() -> None:
    scores = summarize_session([AxisLevels(4, 4, 2, 4)], consistency_level=0)

    assert scores is not None
    assert scores.consistency is None
    assert scores.reliability_score == 50


def test_invalid_consistency_is_treated_as_unknown() -> None:
    assert parse_consistency(7) is None
    assert parse_consistency(None) is None
    assert parse_consistency(3) == 3
