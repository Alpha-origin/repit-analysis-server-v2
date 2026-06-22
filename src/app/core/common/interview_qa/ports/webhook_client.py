"""WebhookClient Port.

파이프라인 완료/실패 시 클라이언트가 지정한 ``callback_url`` 로 결과를
POST 하는 책임을 정의한다. 실제 HTTP 호출 구현은 outbound 어댑터에서.
"""

from __future__ import annotations

from typing import Any, Protocol


class WebhookClient(Protocol):
    """콜백 POST 전송 인터페이스.

    구현체는 1회 재시도 + timeout 처리를 책임진다.
    호출자는 성공/실패 여부만 확인하면 된다.
    """

    async def send(self, url: str, payload: dict[str, Any]) -> bool:
        """주어진 ``url`` 로 ``payload`` 를 JSON POST.

        Args:
            url: 콜백 URL (사용자가 요청 시 넘긴 callback_url).
            payload: 전송할 JSON 본문 (이미 dict 로 직렬화된 상태).

        Returns:
            True - 2xx 응답을 받음.
            False - 재시도 후에도 실패(네트워크 오류/타임아웃/5xx/4xx).
        """
        ...
