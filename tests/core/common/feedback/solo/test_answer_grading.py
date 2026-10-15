from typing import Any

import pytest

from app.core.common.feedback.scoring import AxisLevels
from app.core.common.feedback.solo.answer_grading import _parse_submission
from app.core.common.interview_qa.errors import PipelineError

_SOURCES = {"q-1": ("대기 시간을 줄이려고 비동기로 바꿨고 동기 방식보다 빨랐습니다.",)}


def _blocks(feedbacks: list[dict[str, Any]], consistency: object = 3) -> list[dict[str, Any]]:
    return [
        {
            "type": "tool_use",
            "name": "submit_feedback",
            "input": {
                "overall": {"summary": "총평", "strengths": [], "improvements": [], "consistency": consistency},
                "feedbacks": feedbacks,
            },
        }
    ]


def _entry(question_id: str, scores: object) -> dict[str, Any]:
    return {
        "question_id": question_id, "model_answer": "예시", "comment": "평가", "scores": scores,
        "depth_evidence": {
            "reason": "대기 시간을 줄이려고", "alternatives": "동기 방식보다 빨랐습니다.",
            "limitation": "", "generic_reason": "",
        },
    }


def test_axis_levels_are_parsed_per_question() -> None:
    result = _parse_submission(
        _blocks([_entry("q-1", {"intent": 4, "specificity": 2, "accuracy": None})]),
        ("q-1",),
        _SOURCES,
    )

    assert result["feedbacks"]["q-1"]["axis_levels"] == AxisLevels(4, 3, 2, None)
    assert result["consistency"] == 3


def test_invalid_axis_levels_fail_like_missing_feedback() -> None:
    with pytest.raises(PipelineError) as error:
        _parse_submission(
            _blocks([_entry("q-1", {"intent": 5, "specificity": 2, "accuracy": 4})]),
            ("q-1",),
            _SOURCES,
        )

    assert error.value.status_code == 500


def test_invalid_consistency_does_not_fail_grading() -> None:
    result = _parse_submission(
        _blocks([_entry("q-1", {"intent": 4, "specificity": 2, "accuracy": 4})], consistency="high"),
        ("q-1",),
        _SOURCES,
    )

    assert result["consistency"] is None
