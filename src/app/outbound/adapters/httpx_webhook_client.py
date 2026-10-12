from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.core.common.security.ports import RuntimeSecurityProvider
from app.outbound.adapters.httpx_callback_transport import HttpxCallbackTransport
from app.outbound.adapters.runtime_security_provider import DEVELOPMENT_DEFAULT

logger = logging.getLogger(__name__)


class HttpxWebhookClient:
    def __init__(
        self,
        timeout_seconds: int,
        retry_delay_seconds: int,
        transport: httpx.AsyncBaseTransport | None = None,
        security: RuntimeSecurityProvider | None = None,
    ) -> None:
        # timeout/재시도 delay 는 OutboundProvider 가 Settings 에서 꺼내 전달한다.
        # ``transport`` 는 테스트에서 MockTransport 를 주입하기 위한 hook.
        # ``security`` 는 현재 프로세스의 콜백 토큰·허용/차단 호스트. 없으면 개발 기본값(토큰 없음).
        self._timeout = timeout_seconds
        self._retry_delay = retry_delay_seconds
        self._security = security or DEVELOPMENT_DEFAULT
        self._sender = HttpxCallbackTransport(self._security, transport)

    async def send(self, url: str, payload: dict[str, Any]) -> bool:
        # 시도 순회: index 0 = 첫 시도, index 1 = 재시도.
        # 코드는 학생이 읽기 쉽도록 평범한 for 루프로 작성.
        # 운영(production)에서는 허용 목록에 없는 목적지로 아예 보내지 않는다.
        # 개발/테스트에서는 기존처럼 보내되 토큰은 신뢰 목적지에만 붙는다.
        allow_untrusted = self._security.current().environment != "production"
        for attempt in range(2):
            outcome = await self._sender.post_once(
                url, payload, timeout_seconds=self._timeout, allow_untrusted=allow_untrusted
            )
            if outcome.kind == "delivered":
                logger.info(
                    "webhook.callback.sent",
                    extra={"attempt": attempt, "status_code": outcome.status_code, "job_id": payload.get("jobId")},
                )
                return True
            if outcome.kind == "rejected":
                # 목적지 정책 위반은 재시도해도 바뀌지 않는다. 한 번도 보내지 않고 종료.
                logger.error(
                    "webhook.callback.destination_rejected",
                    extra={"reason": outcome.reason, "job_id": payload.get("jobId")},
                )
                return False
            # 응답 본문·URL 은 서명/개인정보가 섞일 수 있어 로그에 남기지 않는다.
            logger.warning(
                "webhook.callback.attempt_failed",
                extra={"attempt": attempt, "kind": outcome.kind, "status_code": outcome.status_code},
            )
            # 마지막 시도가 아니면 잠시 대기 후 다시 시도.
            if attempt == 0:
                await asyncio.sleep(self._retry_delay)
        # 두 번 다 실패 — 로그만 남기고 종료. 결과는 영구 폐기.
        logger.error("webhook.callback.dropped", extra={"job_id": payload.get("jobId")})
        return False
