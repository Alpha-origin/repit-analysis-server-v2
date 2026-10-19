from __future__ import annotations

from typing import Any

import anthropic

from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicCallResult, AnthropicTextClientError


class AnthropicDocumentClient:
    """Document-only SDK extension; legacy model calls keep their existing adapter and arguments."""

    def __init__(self, api_key: str) -> None:
        self.client = anthropic.AsyncAnthropic(api_key=api_key)

    async def close(self) -> None:
        await self.client.close()

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
    ) -> AnthropicCallResult:
        kwargs: dict[str, Any] = {"model": model, "system": system, "messages": messages, "max_tokens": max_tokens}
        options = (
            ("tools", tools),
            ("tool_choice", tool_choice),
            ("temperature", temperature),
            ("output_config", output_config),
        )
        kwargs.update({key: value for key, value in options if value is not None})
        try:
            raw = await self.client.messages.with_raw_response.create(**kwargs)
            response = raw.parse()
        except anthropic.APIError as exc:
            raise AnthropicTextClientError("문서 분석 모델 호출에 실패했습니다.") from exc
        retry_header = raw.http_response.request.headers.get("x-stainless-retry-count", "0")
        retries = int(retry_header) if retry_header.isdecimal() else 0
        details = response.model_dump().get("stop_details")
        category = details.get("category") if isinstance(details, dict) else None
        return AnthropicCallResult(
            content_blocks=[block.model_dump() for block in response.content],
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            stop_reason=response.stop_reason,
            sdk_retries=retries,
            refusal_category=category if isinstance(category, str) else None,
        )

    async def count_tokens(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> int:
        kwargs: dict[str, Any] = {"model": model, "system": system, "messages": messages, "tools": tools}
        try:
            response = await self.client.messages.count_tokens(**kwargs)
        except anthropic.APIError as exc:
            raise AnthropicTextClientError("문서 입력 토큰을 계산하지 못했습니다.") from exc
        return response.input_tokens
