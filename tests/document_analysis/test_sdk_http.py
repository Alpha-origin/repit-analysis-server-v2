from __future__ import annotations

import json
from typing import Any

import anthropic
import httpx

from app.outbound.adapters.anthropic_document_client import AnthropicDocumentClient


async def test_real_sdk_omits_sampling_and_counts_tokens_without_network() -> None:
    sent: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        if request.url.path.endswith("/count_tokens"):
            return httpx.Response(200, json={"input_tokens": 123})
        return httpx.Response(
            200,
            json={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": "claude-haiku-5-5",
                "content": [{"type": "tool_use", "id": "tool_test", "name": "submit_page_reading", "input": {}}],
                "stop_reason": "tool_use",
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    adapter = AnthropicDocumentClient("test")
    await adapter.client.close()
    adapter.client = anthropic.AsyncAnthropic(
        api_key="test", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
    )
    try:
        response = await adapter.call(
            model="claude-haiku-5-5",
            system="전사",
            messages=[{"role": "user", "content": "자료"}],
            output_config={"effort": "low"},
        )
        count = await adapter.count_tokens(
            model="stub", system="전사", messages=[{"role": "user", "content": "자료"}], tools=[]
        )
    finally:
        await adapter.close()
    assert count == 123
    assert response.input_tokens == 10
    assert response.sdk_retries == 0
    assert sent[0]["output_config"] == {"effort": "low"}
    assert not {"temperature", "top_p", "top_k"} & sent[0].keys()
