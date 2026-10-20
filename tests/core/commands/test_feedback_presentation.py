from typing import Any

import pytest

from tests.company_helpers import company_http
from tests.multi_helpers import feedback_body


@pytest.mark.parametrize("mode", ["solo", "multi"])
@pytest.mark.parametrize("follow_up", [None, "다른 방법은 무엇인가요?", " \n ", "가" * 201, {"bad": 1}])
async def test_follow_up_survives_callback_and_invalid_optional_value_does_not_break_grading(
    mode: str, follow_up: Any
) -> None:
    async with company_http() as (client, webhook, llm):
        tool = "submit_feedback" if mode == "solo" else "submit_multi_feedback"
        llm.outputs[tool]["feedbacks"][0]["follow_up_question"] = follow_up
        response = await client.post(f"/feedback/{mode}", json=feedback_body())
    assert response.status_code == 202
    payload = webhook.payloads[0]
    assert payload["status"] == "succeeded"
    expected = follow_up if follow_up == "다른 방법은 무엇인가요?" else None
    assert payload["result"]["feedbacks"][0]["followUpQuestion"] == expected
    assert "follow_up_question" not in payload["result"]["feedbacks"][0]


@pytest.mark.parametrize("mode", ["solo", "multi"])
async def test_internal_ids_are_corrected_only_in_generated_callback_text(mode: str) -> None:
    body = feedback_body()
    body["questions"][0]["questionId"] = "62"
    body["questions"][0]["content"] = "원래 질문 Q62"
    body["answers"][0]["questionId"] = "62"
    body["answers"][0]["content"] += " Q62 원문은 유지합니다."
    async with company_http() as (client, webhook, llm):
        tool = "submit_feedback" if mode == "solo" else "submit_multi_feedback"
        output = llm.outputs[tool]
        output["feedbacks"][0].update(question_id="62", comment="Q62 근거", follow_up_question="문항 62 대안은?")
        output["overall"]["summary"] = "Q62에서 판단했습니다"
        if mode == "multi":
            output["personas"][0]["comment"] = "Q62 보완"
        response = await client.post(f"/feedback/{mode}", json=body)
    assert response.status_code == 202
    result = webhook.payloads[0]["result"]
    assert result["overall"]["summary"] == "Q1에서 판단했습니다"
    item = result["feedbacks"][0]
    assert item["questionId"] == "62"
    assert item["comment"] == "Q1 근거"
    assert item["followUpQuestion"] == "문항 1 대안은?"
    assert item["questionContent"] == "원래 질문 Q62"
    assert item["userAnswer"] == body["answers"][0]["content"]
    if mode == "multi":
        assert result["personas"][-1]["comment"] == "Q1 보완"
