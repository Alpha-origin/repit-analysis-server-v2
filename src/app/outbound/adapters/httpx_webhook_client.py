from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# 재시도 대기를 몇 배씩 늘릴지. 설정으로 뺄 만큼 조정할 일이 없어 상수로 둔다.
_BACKOFF_MULTIPLIER = 3


def _job_id_of(payload: dict[str, Any]) -> str | None:
    # /generate 계열 콜백은 snake_case(job_id), tailor/feedback 계열은 camelCase(jobId) 다.
    # 한쪽만 보면 나머지 네 엔드포인트의 폐기 로그가 전부 None 으로 남는다.
    job_id = payload.get("jobId") or payload.get("job_id")
    return job_id if isinstance(job_id, str) else None


class HttpxWebhookClient:
    def __init__(
        self,
        timeout_seconds: int,
        retry_attempts: int,
        retry_delay_seconds: int,
        retry_max_delay_seconds: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # timeout/재시도 설정은 OutboundProvider 가 Settings 에서 꺼내 전달한다.
        # ``transport`` 는 테스트에서 MockTransport 를 주입하기 위한 hook.
        self._timeout = timeout_seconds
        self._attempts = max(1, retry_attempts)
        self._retry_delay = retry_delay_seconds
        self._max_delay = retry_max_delay_seconds
        self._transport = transport

    async def send(self, url: str, payload: dict[str, Any]) -> bool:
        # 시도 순회: index 0 = 첫 시도, 이후는 재시도.
        # 코드는 학생이 읽기 쉽도록 평범한 for 루프로 작성.
        for attempt in range(self._attempts):
            ok = await self._post_once(url, payload, attempt)
            if ok:
                return True
            # 마지막 시도였다면 더 기다릴 이유가 없다.
            if attempt == self._attempts - 1:
                break
            delay = self._delay_for(attempt)
            logger.info(
                "webhook.callback.retry_scheduled",
                extra={"url": url, "attempt": attempt, "delay_seconds": delay},
            )
            await asyncio.sleep(delay)
        # 두 번 다 실패 — 로그만 남기고 종료. 결과는 영구 폐기.
        # 어느 작업이 날아갔는지가 이 로그의 존재 이유라, job id 를 반드시 남긴다.
        logger.error(
            "webhook.callback.dropped",
            extra={"url": url, "job_id": _job_id_of(payload)},
        )
        return False

    def _delay_for(self, attempt: int) -> int:
        # 고정 간격은 수신측 재기동 한 번을 못 넘긴다. 몇 배씩 늘리되 상한을 둔다.
        delay: int = self._retry_delay * _BACKOFF_MULTIPLIER**attempt
        return min(delay, self._max_delay)

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
