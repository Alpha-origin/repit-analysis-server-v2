import asyncio
from typing import Any

import pytest

from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicCallResult
from tests.company_helpers import company_body, company_http
from tests.multi_helpers import feedback_body


@pytest.mark.parametrize("mode", ["solo", "multi"])
@pytest.mark.parametrize("fit_state", ["absent", "success", "exception", "invalid"])
async def test_company_fit_is_optional_and_cannot_change_grading_input_or_scores(mode: str, fit_state: str) -> None:
    body = feedback_body()
    async with company_http() as (client, webhook, llm):
        await client.post(f"/feedback/{mode}", json=body)
        baseline = webhook.payloads[0]["result"]
        baseline_grading = llm.calls[0]
    if fit_state != "absent":
        body["company"] = company_body()
    async with company_http() as (client, webhook, llm):
        llm.fail_fit = fit_state == "exception"
        if fit_state == "invalid":
            llm.outputs["submit_company_fit"]["comment"] = ""
        response = await client.post(f"/feedback/{mode}", json=body)
    assert response.status_code == 202
    payload = webhook.payloads[0]
    assert payload["status"] == "succeeded"
    result = payload["result"]
    grading = next(call for call in llm.calls if call["tool_choice"].get("name") != "submit_company_fit")
    assert grading == baseline_grading
    assert "샘플커머스" not in str(grading["messages"])
    assert result["overall"] == baseline["overall"]
    assert result["feedbacks"] == baseline["feedbacks"]
    assert (result["companyFit"] is not None) is (fit_state == "success")
    if fit_state == "absent":
        assert len(llm.calls) == 1
    else:
        assert len(llm.calls) == 2
        fit = next(call for call in llm.calls if call["tool_choice"].get("name") == "submit_company_fit")
        assert fit["temperature"] == 0.2
        assert fit["max_tokens"] == 1500


@pytest.mark.parametrize("mode", ["solo", "multi"])
async def test_grading_failure_remains_a_feedback_failure(mode: str) -> None:
    body = dict(feedback_body(), company=company_body())
    async with company_http() as (client, webhook, llm):
        llm.fail_grading = True
        await client.post(f"/feedback/{mode}", json=body)
    assert webhook.payloads[0]["status"] == "failed"
    assert "result" not in webhook.payloads[0]


@pytest.mark.parametrize("mode", ["solo", "multi"])
async def test_grading_and_company_fit_start_concurrently(mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    body = dict(feedback_body(), company=company_body())
    async with company_http() as (client, webhook, llm):
        grading_started, fit_started = asyncio.Event(), asyncio.Event()
        original_call = llm.call

        async def call(**kwargs: Any) -> AnthropicCallResult:
            if kwargs["tool_choice"].get("name") == "submit_company_fit":
                fit_started.set()
                await asyncio.wait_for(grading_started.wait(), timeout=2)
            else:
                grading_started.set()
                await asyncio.wait_for(fit_started.wait(), timeout=2)
            return await original_call(**kwargs)

        monkeypatch.setattr(llm, "call", call)
        await client.post(f"/feedback/{mode}", json=body)
    assert webhook.payloads[0]["status"] == "succeeded"
    assert webhook.payloads[0]["result"]["companyFit"] is not None


async def test_multi_fit_uses_tech_persona_tone() -> None:
    body = dict(feedback_body(), company=company_body())
    body["personas"][0]["tone"] = "GENTLE"
    body["personas"][1]["tone"] = "PRESSURING"
    async with company_http() as (client, _, llm):
        await client.post("/feedback/multi", json=body)
    fit = next(call for call in llm.calls if call["tool_choice"].get("name") == "submit_company_fit")
    assert "어조(GENTLE)" in fit["messages"][0]["content"]
    assert "어조(PRESSURING)" not in fit["messages"][0]["content"]
