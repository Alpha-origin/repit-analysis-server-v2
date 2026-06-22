"""AnthropicTextClient Port.

Claude 텍스트 모델 호출(메시지 + 선택적 tool-use) 인터페이스.

내부적으로 anthropic SDK 의 ``messages.create`` 를 한 번 호출한다.
Stage 2-3(이미지 LLM 트리아지) 같은 단발 호출, Stage 4(코드 탐색 세션) 같은
누적 호출 모두 동일 인터페이스로 통한다.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel


class AnthropicTextClientError(Exception):
    """Anthropic API 호출 자체가 실패했음(네트워크/4xx/5xx)."""


class AnthropicCallResult(BaseModel):
    """``messages.create`` 호출 결과를 단계 서비스가 다루기 좋게 정리한 형태.

    - ``content_blocks``: SDK 가 돌려준 응답 블록을 dict 로 변환한 리스트.
      각 dict 는 최소 ``type`` 키를 가지며 ``text`` 또는 ``tool_use`` 류이다.
    - ``input_tokens`` / ``output_tokens``: Stage 4 의 누적 토큰 추적에 사용.
    - ``stop_reason``: tool_use / end_turn / max_tokens 등.
    """

    content_blocks: list[dict[str, Any]]
    input_tokens: int
    output_tokens: int
    stop_reason: str | None = None


class AnthropicTextClient(Protocol):
    """텍스트(+ 선택적 tool-use) 호출 인터페이스."""

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
        """단발 호출. 호출자가 messages 전체를 매번 넘긴다(세션 누적은 호출자 몫).

        Raises:
            AnthropicTextClientError: 네트워크/API 오류.
        """
        ...
