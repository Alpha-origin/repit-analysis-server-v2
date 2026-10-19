import asyncio
from typing import Any

import pytest

from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicCallResult
from tests.company_helpers import company_body, company_http
from tests.multi_helpers import feedback_body


@pytest.mark.parametrize("mode", ["solo", "multi"])
@pytest.mark.parametrize("grading_waits_for_timeout", [False, True])
async def test_fit_budget_starts_before_grading_finishes_and_preserves_success(
    mode: str, grading_waits_for_timeout: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    started, cancelled = asyncio.Event(), asyncio.Event()
    async with company_http(fit_timeout_seconds=0.02) as (client, webhook, llm):
        original = llm.call

        async def call(**kwargs: Any) -> AnthropicCallResult:
            if kwargs["tool_choice"].get("name") == "submit_company_fit":
                started.set()
                try:
                    await asyncio.Future[None]()
                finally:
                    cancelled.set()
            await asyncio.wait_for(started.wait(), 2)
            if grading_waits_for_timeout:
                await asyncio.wait_for(cancelled.wait(), 2)
            return await original(**kwargs)

        monkeypatch.setattr(llm, "call", call)
        response = await asyncio.wait_for(
            client.post(f"/feedback/{mode}", json=dict(feedback_body(), company=company_body())), 2
        )
    assert response.status_code == 202
    assert cancelled.is_set()
    assert webhook.payloads[0]["status"] == "succeeded"
    assert webhook.payloads[0]["result"]["companyFit"] is None
    assert webhook.payloads[0]["result"]["overall"]["answeredCount"] > 0


@pytest.mark.parametrize("mode", ["solo", "multi"])
async def test_all_invalid_fit_evidence_keeps_main_feedback(mode: str) -> None:
    async with company_http() as (client, webhook, llm):
        output = llm.outputs["submit_company_fit"]
        for field in ("matched_focus", "gaps"):
            output[field][0]["question_ids"] = ["unanswered", "unknown"]
        response = await client.post(f"/feedback/{mode}", json=dict(feedback_body(), company=company_body()))
    assert response.status_code == 202
    assert webhook.payloads[0]["status"] == "succeeded"
    assert webhook.payloads[0]["result"]["companyFit"] is None
