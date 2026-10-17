from __future__ import annotations

import copy
import difflib
import json
import os
from pathlib import Path
from typing import Any

from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicCallResult

GOLDEN = Path(__file__).parent / "golden"


class RecordingClient:
    """Records the actual model boundary, independently of the company test helpers."""

    def __init__(self, responses: list[list[dict[str, Any]]]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def call(self, **kwargs: Any) -> AnthropicCallResult:
        self.calls.append(copy.deepcopy(kwargs))
        return AnthropicCallResult(
            content_blocks=self.responses[len(self.calls) - 1],
            input_tokens=10,
            output_tokens=10,
            stop_reason="tool_use",
        )


def tool(name: str, value: dict[str, Any], tool_id: str = "fixed-tool") -> list[dict[str, Any]]:
    return [{"type": "tool_use", "id": tool_id, "name": name, "input": value}]


def assert_golden(name: str, value: str | dict[str, Any]) -> None:
    rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    rendered += "\n"
    path = GOLDEN / name
    if os.environ.get("UPDATE_GOLDEN") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
    expected = path.read_text(encoding="utf-8")
    assert expected == rendered, "".join(
        difflib.unified_diff(expected.splitlines(True), rendered.splitlines(True), fromfile=str(path), tofile="actual")
    )


def assert_call(name: str, call: dict[str, Any], *, prompt: bool = True) -> None:
    if prompt:
        content = call["messages"][0]["content"]
        assert isinstance(content, str)
        assert_golden(f"{name}.txt", call["system"] + "\n\n===== USER =====\n\n" + content)
    assert_golden(
        f"call__{name}.json",
        {"tools": call["tools"], "tool_choice": call["tool_choice"]},
    )
