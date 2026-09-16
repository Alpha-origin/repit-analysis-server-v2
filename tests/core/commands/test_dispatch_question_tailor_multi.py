import pytest

from tests.multi_helpers import multi_http, tailor_body, tailor_outputs


@pytest.mark.parametrize("count", [2, 3, 4, 5])
async def test_defaults_generate_two_questions_per_persona_including_legacy_five(count: int) -> None:
    async with multi_http(tailor_outputs((2,) * (count - 1))) as (client, webhook):
        response = await client.post("/questions/tailor/multi", json=tailor_body(count))
    assert response.status_code == 202
    (payload,) = webhook.payloads
    assert payload["status"] == "succeeded"
    assert payload["jobId"] == response.json()["jobId"]
    assert payload["interviewId"] == "iv-1"
    assert "tailored" not in payload["result"]
    questions = payload["result"]["questions"]
    assert len(questions) == count * 2
    assert [q["personaId"] for q in questions] == [f"p-{i}" for i in range(count) for _ in range(2)]
    assert [q["id"] for q in questions] == [1, 2, *range(6, 6 + 2 * (count - 1))]
    assert [q["question"] for q in questions[:2]] == ["재작성 1", "재작성 2"]


@pytest.mark.parametrize(("ids", "first_generated"), [((1, 2), 6), ((5, 9), 10)])
async def test_custom_counts_and_original_ids(ids: tuple[int, int], first_generated: int) -> None:
    body = tailor_body(ids=ids)
    for persona, count in zip(body["otherPersonas"], (1, 3, 2), strict=True):
        persona["questionCount"] = count
    async with multi_http(tailor_outputs((1, 3, 2), ids)) as (client, webhook):
        response = await client.post("/questions/tailor/multi", json=body)
    assert response.status_code == 202
    questions = webhook.payloads[0]["result"]["questions"]
    assert [q["id"] for q in questions] == [*ids, *range(first_generated, first_generated + 6)]
    assert [q["personaId"] for q in questions] == ["p-0", "p-0", "p-1", "p-2", "p-2", "p-2", "p-3", "p-3"]


@pytest.mark.parametrize("failed_tool", ["submit_tailored_questions", "submit_generated_questions"])
async def test_stage_failure_sends_failure_callback_without_partial_result(failed_tool: str) -> None:
    outputs = tailor_outputs((2, 2, 2))
    outputs[failed_tool] = {"questions": []}
    async with multi_http(outputs) as (client, webhook):
        response = await client.post("/questions/tailor/multi", json=tailor_body())
    assert response.status_code == 202
    (payload,) = webhook.payloads
    assert payload["status"] == "failed"
    assert payload["error"]["statusCode"] == 500
    assert "result" not in payload
