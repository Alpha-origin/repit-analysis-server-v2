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


async def test_missing_persona_produces_failure_callback() -> None:
    output = feedback_output()
    output["personas"].pop(0)
    async with multi_http({"submit_multi_feedback": output}) as (client, webhook):
        response = await client.post("/feedback/multi", json=feedback_body())
    assert response.status_code == 202
    assert webhook.payloads[0]["status"] == "failed"
    assert webhook.payloads[0]["error"]["statusCode"] == 500
