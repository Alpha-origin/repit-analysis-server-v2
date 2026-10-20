from __future__ import annotations

from typing import Any, Protocol

from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicCallResult


class DocumentModelClient(Protocol):
    async def call(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
        max_tokens: int = 4096,
        temperature: float | None = None,
        output_config: dict[str, Any] | None = None,
    ) -> AnthropicCallResult: ...

    async def count_tokens(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> int: ...
