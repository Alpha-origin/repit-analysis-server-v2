from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel


class AnthropicTextClientError(Exception):
    def __init__(self, message: str, *, kind: Literal["transient", "configuration", "unknown"] = "unknown") -> None:
        super().__init__(message)
        self.kind = kind


class AnthropicCallResult(BaseModel):
    content_blocks: list[dict[str, Any]]
    input_tokens: int
    output_tokens: int
    stop_reason: str | None = None
    sdk_retries: int = 0
    refusal_category: str | None = None


class AnthropicTextClient(Protocol):
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
    ) -> AnthropicCallResult: ...
