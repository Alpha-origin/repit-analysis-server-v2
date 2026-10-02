from __future__ import annotations

import asyncio
import logging

from app.core.common.security.dto import AttemptOutcome
from app.core.common.security.ports import CallbackTransport
from app.core.common.video.ports import CallbackClaim, DeliveryDecision, VideoRepository

logger = logging.getLogger(__name__)

# 408/429/5xx 와 네트워크·시간초과만 재시도한다. redirect 와 나머지 4xx 는 전달 실패로 끝낸다.
RETRYABLE_STATUS = frozenset({408, 429})
IO_TIMEOUT_SECONDS = 10.0


def classify(outcome: AttemptOutcome) -> DeliveryDecision:
    if outcome.kind == "delivered":
        return "delivered"
    if outcome.kind in {"network", "timeout"}:
        return "retry"
    if outcome.kind == "http_status" and outcome.status_code is not None:
        status = outcome.status_code
        if status in RETRYABLE_STATUS or 500 <= status <= 599:  # noqa: PLR2004
            return "retry"
    return "failed"


def outcome_code(outcome: AttemptOutcome) -> str:
    if outcome.status_code is not None:
        return f"http_{outcome.status_code}"
    return outcome.reason or outcome.kind


class DeliverVideoCallback:
    """Sends a stored terminal body once per reserved attempt. Never re-runs analysis.

    Waiting between attempts happens in the database (``next_at``), so a 900-second backoff does not
    occupy a worker slot. Delivery failure never changes or removes the stored result.
    """

    def __init__(
        self,
        repository: VideoRepository,
        transport: CallbackTransport,
        capacity: int = 1,
        lease_seconds: int = 180,
    ) -> None:
        self.repository = repository
        self.transport = transport
        self.capacity = capacity
        self.lease_seconds = lease_seconds

    async def run_once(self) -> bool:
        claim = await asyncio.to_thread(self.repository.claim_callback, self.capacity, self.lease_seconds)
        if claim is None:
            return False
        outcome = await self._send(claim)
        decision = classify(outcome)
        code = outcome_code(outcome)
        stored = await asyncio.to_thread(self.repository.finish_callback, claim, decision, code)
        logger.info(
            "video.callback.attempt job=%s reservation=%d/%d decision=%s outcome=%s stored=%s",
            claim.job_id,
            claim.reservation,
            claim.max_attempts,
            decision,
            code,
            stored,
        )
        return True

    async def _send(self, claim: CallbackClaim) -> AttemptOutcome:
        return await self.transport.post_once(
            claim.url,
            claim.body,
            timeout_seconds=min(IO_TIMEOUT_SECONDS, claim.deadline_seconds),
            total_deadline_seconds=claim.deadline_seconds,
            allow_untrusted=False,
        )
