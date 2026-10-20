from typing import Any

import pytest

from app.core.commands.dispatch_question_cycle import DispatchQuestionCycle
from app.core.common.question_cycle.dto import QuestionCycleRequest
from app.core.common.question_cycle.generate import QuestionCycleGenerate
from tests.cycle_helpers import (
    SequencedClient,
    cycle_body,
    cycle_http,
    multi_entries,
    refined_profile,
    solo_entries,
    solo_submissions,
)


async def test_solo_cycle_success_callback_is_sorted_camel_case() -> None:
    async with cycle_http(solo_submissions()) as (client, webhook, llm):
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
    batches = solo_submissions()
    broken = {"questions": batches[0]["questions"][:-1]}
    async with cycle_http([broken, *batches]) as (client, webhook, llm):
        await client.post("/questions/cycle", json=cycle_body())

    (payload,) = webhook.payloads
    assert payload["status"] == "succeeded"
    assert len(llm.calls) == 4
    retry_turn = llm.calls[1]["messages"][-1]["content"][0]
    assert retry_turn["type"] == "tool_result"
    assert retry_turn["tool_use_id"] == "t-1"
    assert "정확히 5개" in retry_turn["content"]


async def test_repeated_recent_question_is_retried_with_feedback() -> None:
    # cycle_body 의 제외 질문 "예전 질문" 을 문장부호만 바꿔 다시 낸다.
    batches = solo_submissions()
    repeated = [dict(q) for q in batches[0]["questions"]]
    repeated[0]["question"] = "예전 질문?"
    async with cycle_http([{"questions": repeated}, *batches]) as (client, webhook, llm):
        await client.post("/questions/cycle", json=cycle_body())

    (payload,) = webhook.payloads
    assert payload["status"] == "succeeded"
    assert len(llm.calls) == 4
    assert "최근 질문과 같다" in llm.calls[1]["messages"][-1]["content"][0]["content"]


async def test_second_violation_sends_failure_callback() -> None:
    broken = {"questions": solo_submissions()[0]["questions"][:-1]}
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
    async with cycle_http(solo_submissions(entries)) as (client, webhook, llm):
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


async def test_last_set_failure_never_sends_partial_success() -> None:
    batches = solo_submissions()
    broken = {"questions": batches[2]["questions"][:-1]}
    async with cycle_http([*batches[:2], broken, broken]) as (client, webhook, llm):
        await client.post("/questions/cycle", json=cycle_body())

    (payload,) = webhook.payloads
    assert payload["status"] == "failed"
    assert "result" not in payload
    assert payload["error"]["statusCode"] == 500
    assert len(llm.calls) == 4


async def test_dropped_callback_is_not_logged_as_completed(caplog: pytest.LogCaptureFixture) -> None:

    class DroppedWebhook:
        async def send(self, url: str, payload: dict[str, Any]) -> bool:
            return False

    llm = SequencedClient(solo_submissions())
    dispatcher = DispatchQuestionCycle(DroppedWebhook(), QuestionCycleGenerate(llm, "stub", 12288, 1, 400, 30, 600))
    request = QuestionCycleRequest(profile=refined_profile(), mode="SOLO", callback_url="https://example.com/callback")
    caplog.set_level("INFO")
    await dispatcher.execute("dropped-job", request)
    assert "question_cycle.dispatch.callback_dropped job_id=dropped-job status=succeeded" in caplog.text
    assert "question_cycle.dispatch.done" not in caplog.text
