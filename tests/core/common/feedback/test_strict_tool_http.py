from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import anthropic
import httpx
import pytest

from app.core.common.company.fit.tools import SUBMIT_COMPANY_FIT_TOOL
from app.core.common.feedback.multi.tools import feedback_tool_for_job as multi_tool
from app.core.common.feedback.solo.tools import feedback_tool_for_job as solo_tool
from app.core.common.job_spec import JOBS
from app.outbound.adapters.anthropic_text_client_impl import AnthropicTextClientImpl

_UNSUPPORTED = {
    "minLength",
    "maxLength",
    "maxItems",
    "uniqueItems",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
}
_FEEDBACK_TOOLS = [
    pytest.param(factory(role), id=f"{mode}-{role}")
    for mode, factory in (("solo", solo_tool), ("multi", multi_tool))
    for role in (None, *JOBS)
]


def _schema_errors(schema: dict[str, Any], path: str = "$") -> list[str]:
    # Anthropic strict supports minItems 0/1 but no other length/count/numeric bounds.
    errors = [f"{path}.{key}" for key in sorted(_UNSUPPORTED & schema.keys())]
    if "minItems" in schema and schema["minItems"] not in (0, 1):
        errors.append(f"{path}.minItems")
    if schema.get("type") == "object" and schema.get("additionalProperties") is not False:
        errors.append(f"{path}.additionalProperties")
    for name, child in schema.get("properties", {}).items():
        errors.extend(_schema_errors(child, f"{path}.{name}"))
    for name, child in schema.get("$defs", {}).items():
        errors.extend(_schema_errors(child, f"{path}.$defs.{name}"))
    if isinstance(schema.get("items"), dict):
        errors.extend(_schema_errors(schema["items"], f"{path}.items"))
    for key in ("anyOf", "allOf"):
        for index, child in enumerate(schema.get(key, [])):
            errors.extend(_schema_errors(child, f"{path}.{key}[{index}]"))
    return errors


@pytest.mark.parametrize("tool", [pytest.param(SUBMIT_COMPANY_FIT_TOOL, id="company"), *_FEEDBACK_TOOLS])
async def test_real_sdk_sends_supported_strict_tools_without_network(tool: dict[str, Any]) -> None:
    sent: list[dict[str, Any]] = []
    original = deepcopy(tool)

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        errors = _schema_errors(body["tools"][0]["input_schema"])
        if errors:
            return httpx.Response(
                400,
                json={
                    "type": "error",
                    "error": {"type": "invalid_request_error", "message": f"Unsupported strict schema: {errors}"},
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": "stub",
                "content": [{"type": "tool_use", "id": "tool_test", "name": tool["name"], "input": {}}],
                "stop_reason": "tool_use",
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    adapter = AnthropicTextClientImpl("test")
    await adapter._client.close()
    adapter._client = anthropic.AsyncAnthropic(
        api_key="test",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    try:
        result = await adapter.call(
            model="stub",
            system="test",
            messages=[{"role": "user", "content": "자료"}],
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
        )
    finally:
        await adapter.close()
    assert result.stop_reason == "tool_use"
    assert len(sent) == 1
    assert sent[0]["tools"] == [original]
    assert sent[0]["tools"][0]["strict"] is True
    assert tool == original


def test_schema_guard_checks_nested_constraints_and_supported_min_items() -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "text": {"anyOf": [{"type": "string", "maxLength": 200}, {"type": "null"}]},
            "items": {"type": "array", "maxItems": 3, "items": {"type": "string"}},
            "ids": {"type": "array", "minItems": 1, "items": {"type": "string"}},
        },
    }
    assert _schema_errors(schema) == ["$.text.anyOf[0].maxLength", "$.items.maxItems"]
