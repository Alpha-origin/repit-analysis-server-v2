from typing import Any

import pytest

from app.core.common.feedback.multi.answer_grading import _parse_submission as parse_multi
from app.core.common.feedback.solo.answer_grading import _parse_submission as parse_solo
from app.core.common.interview_qa.errors import PipelineError


def _parse(mode: str, evidence: object, sources: tuple[str, ...]) -> dict[str, Any]:
    entry = {
        "question_id": "q-1",
        "scores": {"intent": 4, "specificity": 3, "accuracy": None},
        "depth_evidence": evidence,
    }
    name = "submit_feedback" if mode == "solo" else "submit_multi_feedback"
    blocks = [{
        "type": "tool_use",
        "name": name,
        "input": {
            "overall": {"summary": "총평", "consistency": None},
            "feedbacks": [entry],
            "personas": [{"persona_id": "p-1"}],
        },
    }]
    if mode == "solo":
        return parse_solo(blocks, ("q-1",), {"q-1": sources})
    return parse_multi(blocks, ("q-1",), ("p-1",), {"q-1": sources})


@pytest.mark.parametrize("mode", ["solo", "multi"])
def test_production_parsers_calculate_depth_from_source_checked_evidence(mode: str) -> None:
    evidence = {"reason": "응답 지연을 줄이려고", "alternatives": "", "limitation": "", "generic_reason": ""}
    result = _parse(mode, evidence, ("응답 지연을 줄이려고 캐시를 적용했습니다.",))
    assert result["feedbacks"]["q-1"]["axis_levels"].depth == 2


@pytest.mark.parametrize("mode", ["solo", "multi"])
@pytest.mark.parametrize(
    "evidence", [None, {"reason": "다른 문항의 이유", "alternatives": "", "limitation": "", "generic_reason": ""}]
)
def test_missing_or_unmatched_depth_evidence_fails_the_submission(mode: str, evidence: object) -> None:
    with pytest.raises(PipelineError):
        _parse(mode, evidence, ("현재 문항의 실제 답변",))
