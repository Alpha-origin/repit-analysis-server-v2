import asyncio
from typing import Any

import pytest

from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicCallResult, AnthropicTextClientError
from tests.company_helpers import company_body, company_http
from tests.multi_helpers import feedback_body


@pytest.mark.parametrize("mode", ["solo", "multi"])
@pytest.mark.parametrize("failure", ["grading", "request_cancelled"])
async def test_unused_company_call_is_cancelled_and_drained(
    mode: str,
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fit_started, grading_started, fit_cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async with company_http() as (client, webhook, llm):
        original_call = llm.call

        async def call(**kwargs: Any) -> AnthropicCallResult:
            if kwargs["tool_choice"].get("name") == "submit_company_fit":
                fit_started.set()
                try:
                    await asyncio.Future[None]()
                finally:
                    fit_cancelled.set()
            else:
                await fit_started.wait()
                grading_started.set()
                if failure == "grading":
                    raise AnthropicTextClientError("채점 실패")
                await asyncio.Future[None]()
            return await original_call(**kwargs)

        monkeypatch.setattr(llm, "call", call)
        request = asyncio.create_task(
            client.post(f"/feedback/{mode}", json=dict(feedback_body(), company=company_body()))
        )
        try:
            if failure == "grading":
                response = await asyncio.wait_for(request, timeout=2)
                assert response.status_code == 202
                assert webhook.payloads[0]["status"] == "failed"
                assert webhook.payloads[0]["error"]["statusCode"] == 502
            else:
                await asyncio.wait_for(grading_started.wait(), timeout=2)
                request.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await request
                assert webhook.payloads == []
            assert fit_cancelled.is_set()
        finally:
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)
