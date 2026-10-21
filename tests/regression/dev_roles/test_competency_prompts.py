from pathlib import Path

import pytest

from app.core.common.feedback.competency.prompt import system_prompt
from app.core.common.feedback.competency.tools import feedback_tool


@pytest.mark.parametrize("multi", [False, True])
@pytest.mark.parametrize("job", ["BACKEND", "GEN_FINANCE"])
def test_competency_prompt_golden(job: str, multi: bool) -> None:
    path = Path(__file__).parent / "golden" / f"competency_v1_{job}_{'multi' if multi else 'solo'}.txt"
    prompt = system_prompt(job, multi=multi)
    assert path.read_text() == prompt + "\n"
    assert "depth_evidence" not in prompt
    assert "[축별 등급 기준]" not in prompt
    assert "[역량별 근거 기준]" in prompt
    assert "세 축 등급" not in prompt
    if job == "GEN_FINANCE":
        assert "직무 지식" in prompt
    tool = feedback_tool(job, multi=multi)
    assert tool["strict"] is True
    answer = tool["input_schema"]["properties"]["feedbacks"]["items"]
    assert "scores" not in answer["properties"]
    assert "depth_evidence" not in answer["properties"]
    assert set(answer["properties"]) == set(answer["required"])
    evidence = answer["properties"]["evidence"]
    assert len(evidence["required"]) == 6
