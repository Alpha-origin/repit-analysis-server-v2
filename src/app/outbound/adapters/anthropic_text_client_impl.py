"""AnthropicTextClient 구현체 — 공식 ``anthropic`` SDK 의 ``AsyncAnthropic.messages.create`` 호출.

응답을 dict 기반 ``AnthropicCallResult`` 로 정규화해 도메인 서비스가 SDK 객체 타입을
몰라도 다룰 수 있게 한다.
"""

from __future__ import annotations

import logging
from typing import Any

import anthropic

from app.core.common.interview_qa.ports.anthropic_text_client import (
    AnthropicCallResult,
    AnthropicTextClientError,
)

logger = logging.getLogger(__name__)


class AnthropicTextClientImpl:
    """``AnthropicTextClient`` Protocol 의 SDK 구현체."""

    def __init__(self, api_key: str) -> None:
        # AsyncAnthropic 은 자체 httpx 풀을 들고 있으므로 한 번만 만들어 재사용한다.
        # Request 스코프 어댑터지만 같은 요청 안에서 여러 번 호출되는 패턴(Stage 4) 에서도
        # 풀 재사용으로 latency 가 줄어든다.
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    async def call(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
        max_tokens: int = 4096,
    ) -> AnthropicCallResult:
        """SDK 호출 + 응답 정규화."""
        # SDK 는 None 인자를 받지 않는 경우가 있어 키워드 dict 로 모은 뒤 unpack.
        # tools / tool_choice 가 없으면 키 자체를 빼서 호출한다.
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
        if tools is not None:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice

        try:
            response = await self._client.messages.create(**kwargs)
        except anthropic.APIError as exc:
            # APIStatusError(4xx/5xx), APIConnectionError(네트워크), APITimeoutError 등 모두 포괄.
            logger.warning(
                "anthropic_text.api_error",
                extra={"model": model, "error": str(exc)},
            )
            raise AnthropicTextClientError(f"Anthropic API 호출 실패: {exc}") from exc

        # response.content 는 SDK 의 ContentBlock 객체 리스트. ``model_dump`` 로
        # 단순 dict 화해 도메인 코드에서 SDK 타입에 의존하지 않도록 한다.
        content_blocks = [block.model_dump() for block in response.content]
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        logger.info(
            "anthropic_text.usage model=%s input_tokens=%s output_tokens=%s total_tokens=%s stop_reason=%s",
            model,
            input_tokens,
            output_tokens,
            input_tokens + output_tokens,
            response.stop_reason,
            extra={
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "stop_reason": response.stop_reason,
                "tool_choice": tool_choice.get("name") if isinstance(tool_choice, dict) else None,
            },
        )
        return AnthropicCallResult(
            content_blocks=content_blocks,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            stop_reason=response.stop_reason,
        )
