from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class HttpxWebhookClient:
    def __init__(
        self,
        timeout_seconds: int,
        retry_delay_seconds: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # timeout/재시도 delay 는 OutboundProvider 가 Settings 에서 꺼내 전달한다.
        # ``transport`` 는 테스트에서 MockTransport 를 주입하기 위한 hook.
        self._timeout = timeout_seconds
        self._retry_delay = retry_delay_seconds
        self._transport = transport

    async def send(self, url: str, payload: dict[str, Any]) -> bool:
        # 시도 순회: index 0 = 첫 시도, index 1 = 재시도.
        # 코드는 학생이 읽기 쉽도록 평범한 for 루프로 작성.
        for attempt in range(2):
            ok = await self._post_once(url, payload, attempt)
            if ok:
                return True
            # 마지막 시도가 아니면 잠시 대기 후 다시 시도.
            if attempt == 0:
                await asyncio.sleep(self._retry_delay)
        # 두 번 다 실패 — 로그만 남기고 종료. 결과는 영구 폐기.
        logger.error(
            "webhook.callback.dropped",
            extra={"url": url, "job_id": payload.get("job_id")},
        )
        return False

    async def _post_once(self, url: str, payload: dict[str, Any], attempt: int) -> bool:
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                response = await client.post(url, json=payload)
        except httpx.HTTPError as exc:
            # 네트워크/타임아웃 류는 재시도 가치가 있으므로 단순히 실패만 기록.
            logger.warning(
                "webhook.callback.network_error",
                extra={"url": url, "attempt": attempt, "error": str(exc)},
            )
            return False

        # httpx 의 ``is_success`` 는 200~299 응답에 대해 True 를 반환한다.
        if response.is_success:
            logger.info(
                "webhook.callback.sent",
                extra={"url": url, "attempt": attempt, "status_code": response.status_code},
            )
            return True

        # 200~299 아닌 응답은 실패. 본문은 일부만 잘라 로그(과대 로깅 방지).
        logger.warning(
            "webhook.callback.non_2xx",
            extra={
                "url": url,
                "attempt": attempt,
                "status_code": response.status_code,
                "body_preview": response.text[:200],
            },
        )
        return False
