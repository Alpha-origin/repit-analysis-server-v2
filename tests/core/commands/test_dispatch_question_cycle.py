from tests.cycle_helpers import cycle_body, cycle_http, multi_entries, refined_profile, solo_entries


async def test_solo_cycle_success_callback_is_sorted_camel_case() -> None:
    async with cycle_http([{"questions": solo_entries()}]) as (client, webhook, llm):
        response = await client.post("/questions/cycle", json=cycle_body())

    assert response.status_code == 202
    (payload,) = webhook.payloads
    assert payload["status"] == "succeeded"
    assert payload["jobId"] == response.json()["jobId"]
    result = payload["result"]
    assert result["mode"] == "SOLO"
    assert len(result["questions"]) == 15
    first = result["questions"][0]
    assert set(first) == {"setNo", "category", "question", "intention", "expectedAnswer", "basedOn"}
    assert (first["setNo"], first["category"]) == (1, "tech_choice")
    # 끝 슬래시를 지운 경로로 돌려준다.
    structure = next(q for q in result["questions"] if q["category"] == "structure")
    assert structure["basedOn"] == ["order-api/src/main/java/order"]
    # 빈 제외 질문은 버리고 나머지는 프롬프트에 싣는다.
    prompt = llm.calls[0]["messages"][0]["content"]
    assert "- 예전 질문" in prompt
    assert "포트폴리오 주장 점검" in prompt


async def test_multi_cycle_success() -> None:
    async with cycle_http([{"questions": multi_entries()}]) as (client, webhook, _):
        await client.post("/questions/cycle", json=cycle_body("MULTI"))

    (payload,) = webhook.payloads
    assert payload["status"] == "succeeded"
    assert [q["setNo"] for q in payload["result"]["questions"]] == [1, 1, 2, 2, 3, 3]


async def test_violation_is_retried_once_with_feedback() -> None:
    broken = solo_entries()[:-1]
    async with cycle_http([{"questions": broken}, {"questions": solo_entries()}]) as (client, webhook, llm):
        await client.post("/questions/cycle", json=cycle_body())

    (payload,) = webhook.payloads
    assert payload["status"] == "succeeded"
    assert len(llm.calls) == 2
    retry_turn = llm.calls[1]["messages"][-1]["content"][0]
    assert retry_turn["type"] == "tool_result"
    assert retry_turn["tool_use_id"] == "t-1"
    assert "정확히 15개" in retry_turn["content"]


async def test_second_violation_sends_failure_callback() -> None:
    broken = {"questions": solo_entries()[:-1]}
    async with cycle_http([broken, broken]) as (client, webhook, llm):
        await client.post("/questions/cycle", json=cycle_body())

    (payload,) = webhook.payloads
    assert payload["status"] == "failed"
    assert payload["error"]["statusCode"] == 500
    assert len(llm.calls) == 2


async def test_unknown_schema_version_fails_with_422_without_llm_call() -> None:
    body = cycle_body()
    body["profile"]["schemaVersion"] = 2
    async with cycle_http([]) as (client, webhook, llm):
        await client.post("/questions/cycle", json=body)

    (payload,) = webhook.payloads
    assert payload["error"]["statusCode"] == 422
    assert llm.calls == []


async def test_profile_with_too_few_categories_fails_with_422() -> None:
    profile = refined_profile().model_copy(update={"tech_stack": [], "troubleshootings": [], "integrations": []})
    profile = profile.model_copy(update={"structure_notes": []})
    async with cycle_http([]) as (client, webhook, llm):
        await client.post("/questions/cycle", json=cycle_body(profile=profile))

    (payload,) = webhook.payloads
    assert payload["error"]["statusCode"] == 422
    assert llm.calls == []


async def test_tool_schema_only_offers_available_categories() -> None:
    profile = refined_profile(drop=("troubleshootings", "integrations"))
    entries = solo_entries(("tech_choice", "implementation", "structure"))
    async with cycle_http([{"questions": entries}]) as (client, webhook, llm):
        await client.post("/questions/cycle", json=cycle_body(profile=profile))

    assert webhook.payloads[0]["status"] == "succeeded"
    (tool,) = llm.calls[0]["tools"]
    category_enum = tool["input_schema"]["properties"]["questions"]["items"]["properties"]["category"]["enum"]
    assert category_enum == ["tech_choice", "implementation", "structure"]
    assert "쓸 수 없는 카테고리(재료 없음): troubleshooting, integration" in llm.calls[0]["messages"][0]["content"]


async def test_invalid_mode_is_rejected_synchronously() -> None:
    body = cycle_body()
    body["mode"] = "PAIR"
    async with cycle_http([]) as (client, webhook, _):
        response = await client.post("/questions/cycle", json=body)

    assert response.status_code == 422
    assert webhook.payloads == []
