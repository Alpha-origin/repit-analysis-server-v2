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


async def test_intention_is_always_filled_in_result() -> None:
    body = tailor_body()
    # 1번은 질문 풀에서 온 원질문(intention 있음), 2번은 레거시 원질문(intention 없음).
    body["questions"][0]["intention"] = "캐시 선택 근거를 대안과 비교해 설명할 수 있는지"
    async with multi_http(tailor_outputs((2, 2, 2))) as (client, webhook):
        response = await client.post("/questions/tailor/multi", json=body)
    assert response.status_code == 202
    questions = webhook.payloads[0]["result"]["questions"]
    assert questions[0]["intention"] == "캐시 선택 근거를 대안과 비교해 설명할 수 있는지"
    assert questions[1]["intention"] == "선택 근거"
    assert [q["intention"] for q in questions[2:4]] == ["협업 판단 1-0", "협업 판단 1-1"]
    assert all(q["expectedAnswer"] for q in questions)


async def test_generated_question_without_intention_fails() -> None:
    outputs = tailor_outputs((2, 2, 2))
    for entry in outputs["submit_generated_questions"]["questions"]:
        entry.pop("intention")
    async with multi_http(outputs) as (client, webhook):
        await client.post("/questions/tailor/multi", json=tailor_body())
    (payload,) = webhook.payloads
    assert payload["status"] == "failed"


@pytest.mark.parametrize("with_spoken", [True, False])
async def test_spoken_text_in_technical_and_generated_callback_questions(with_spoken: bool) -> None:
    outputs = tailor_outputs((2, 2, 2))
    if with_spoken:
        for entry in outputs["submit_tailored_questions"]["questions"]:
            entry["spoken_text"] = f" 기술 발음 {entry['id']} "
        for entry in outputs["submit_generated_questions"]["questions"]:
            entry["spoken_text"] = f" 신규 발음 {entry['question']} "
    async with multi_http(outputs) as (client, webhook):
        response = await client.post("/questions/tailor/multi", json=tailor_body())

    assert response.status_code == 202
    (payload,) = webhook.payloads
    assert payload["status"] == "succeeded"
    questions = payload["result"]["questions"]
    assert all(
        set(q) == {"id", "personaId", "category", "question", "spokenText", "intention", "expectedAnswer", "basedOn"}
        for q in questions
    )
    if with_spoken:
        assert [q["spokenText"] for q in questions] == [
            "기술 발음 1",
            "기술 발음 2",
            *[f"신규 발음 질문 {index}-{number}" for index in (1, 2, 3) for number in (0, 1)],
        ]
    else:
        assert all(q["spokenText"] is None for q in questions)
