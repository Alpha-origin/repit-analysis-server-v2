import pytest

from app.core.common.feedback.multi.prompt import SYSTEM_PROMPT as MULTI_PROMPT
from app.core.common.feedback.multi.tools import SUBMIT_MULTI_FEEDBACK_TOOL
from app.core.common.feedback.solo.prompt import SYSTEM_PROMPT as SOLO_PROMPT
from app.core.common.feedback.solo.tools import SUBMIT_FEEDBACK_TOOL


@pytest.mark.parametrize("tool", [SUBMIT_FEEDBACK_TOOL, SUBMIT_MULTI_FEEDBACK_TOOL])
def test_accuracy_applicability_matches_the_common_rubric(tool: dict) -> None:
    assert tool["strict"] is True
    root = tool["input_schema"]
    feedback = root["properties"]["feedbacks"]["items"]
    axes = feedback["properties"]["scores"]
    properties = axes["properties"]

    assert root["additionalProperties"] is False
    assert feedback["additionalProperties"] is False
    assert axes["additionalProperties"] is False
    assert "accuracy는 null" in properties["intent"]["description"]
    assert "실제 답변" in properties["accuracy"]["description"]
    assert "intent 0이거나 그런 주장이 없으면 null" in properties["accuracy"]["description"]
    # strict API에서 호환되는 기존 nullable 표현도 유지한다.
    assert properties["accuracy"]["anyOf"] == [
        {"type": "integer", "enum": [0, 1, 2, 3, 4]},
        {"type": "null"},
    ]


@pytest.mark.parametrize("prompt", [SOLO_PROMPT, MULTI_PROMPT])
def test_prompts_include_applicability_and_boundary_rules(prompt: str) -> None:
    assert "의도 충족이 0인 문항은 depth와 specificity를 0, accuracy를 null" in prompt
    assert "나머지 축도 모두 0" not in prompt
    assert "부정 사실의 설명은 거절과 구분" in prompt
    assert "질문 자체가 참여 여부나 결정 유무만 묻는다면" in prompt
    assert "질문에 제시된 문제나 행동의 일반적인 효용" in prompt
    assert "행동·검증 절차뿐이므로 depth 0" in prompt
    assert "횟수·도구명·적용 대상 한 가지" in prompt
    assert "횟수만 묻는 단일 사실 질문" in prompt
