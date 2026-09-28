import pytest

from tests.multi_helpers import feedback_body, feedback_output, multi_http


@pytest.mark.parametrize("count", [2, 3, 4, 5])
async def test_all_personas_in_request_order_including_zero_question_and_legacy(count: int) -> None:
    async with multi_http({"submit_multi_feedback": feedback_output(count)}) as (client, webhook):
        response = await client.post("/feedback/multi", json=feedback_body(count))
    assert response.status_code == 202
    (payload,) = webhook.payloads
    assert payload["status"] == "succeeded"
    assert payload["jobId"] == response.json()["jobId"]
    result = payload["result"]
    assert [p["personaId"] for p in result["personas"]] == [f"p-{i}" for i in range(count)]
    assert result["personas"][-1]["comment"] == "담당 문항 없음"
    assert result["personas"][-1]["score"] == 0
    assert result["feedbacks"][0]["personaId"] == "p-0"
    assert result["overall"]["answeredCount"] == 1
    # 등급 4/3/2/4 → 축 점수 100/75/50/100 → (100x35 + 75x25 + 50x25 + 100x15) / 100 = 81.25
    assert result["overall"]["totalScore"] == 81
    assert result["overall"]["intentAlignmentScore"] == 100
    # 답변이 1개라 일관성을 판단할 수 없으므로 구체성 점수를 그대로 쓴다.
    assert result["overall"]["reliabilityScore"] == 50
    assert result["personas"][0]["score"] == 81
    # 축별 산출 근거는 API 계약을 바꾸기 전까지 콜백에 싣지 않는다.
    assert "consistency" not in result["overall"]
    assert "scores" not in result["feedbacks"][0]


async def test_accuracy_is_ignored_for_non_tech_persona_questions() -> None:
    body = feedback_body()
    body["questions"][0]["personaId"] = "p-1"  # HR
    async with multi_http({"submit_multi_feedback": feedback_output()}) as (client, webhook):
        await client.post("/feedback/multi", json=body)
    result = webhook.payloads[0]["result"]
    # 정확성 축이 빠지고 35:25:25 로 다시 나눈다: (100x35 + 75x25 + 50x25) / 85 = 77.9
    assert result["overall"]["totalScore"] == 78
    assert result["personas"][1]["score"] == 78
    assert result["personas"][0]["score"] == 0


async def test_invalid_axis_level_produces_failure_callback() -> None:
    output = feedback_output()
    output["feedbacks"][0]["scores"]["depth"] = 5
    async with multi_http({"submit_multi_feedback": output}) as (client, webhook):
        await client.post("/feedback/multi", json=feedback_body())
    assert webhook.payloads[0]["status"] == "failed"
    assert webhook.payloads[0]["error"]["statusCode"] == 500


async def test_missing_persona_produces_failure_callback() -> None:
    output = feedback_output()
    output["personas"].pop(0)
    async with multi_http({"submit_multi_feedback": output}) as (client, webhook):
        response = await client.post("/feedback/multi", json=feedback_body())
    assert response.status_code == 202
    assert webhook.payloads[0]["status"] == "failed"
    assert webhook.payloads[0]["error"]["statusCode"] == 500
