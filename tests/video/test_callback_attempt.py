from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from app.core.commands.deliver_video_callback import classify
from app.outbound.adapters.httpx_callback_transport import HttpxCallbackTransport
from tests.video.helpers import CALLBACK_URL, security


async def attempt(status: int) -> tuple[str, int]:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, headers={"Location": "https://elsewhere.example.com/"})

    transport = HttpxCallbackTransport(security(), httpx.MockTransport(handler))
    outcome = await transport.post_once(CALLBACK_URL, {"jobId": "j"}, timeout_seconds=5, total_deadline_seconds=15)
    return classify(outcome), calls


@pytest.mark.asyncio
async def test_single_attempt_and_status_classification() -> None:
    expected = {
        200: "delivered",
        204: "delivered",
        299: "delivered",
        408: "retry",
        429: "retry",
        500: "retry",
        503: "retry",
        301: "failed",
        307: "failed",
        400: "failed",
        401: "failed",
        403: "failed",
        404: "failed",
    }
    for status, decision in expected.items():
        assert await attempt(status) == (decision, 1), status


@pytest.mark.asyncio
async def test_total_deadline_is_not_io_inactivity_timeout() -> None:
    async def trickle(request: httpx.Request) -> httpx.Response:
        # Each step is far below the 5 s I/O timeout; only the whole-attempt deadline can stop it.
        for _ in range(200):
            await asyncio.sleep(0.01)
        return httpx.Response(200)

    transport = HttpxCallbackTransport(security(), httpx.MockTransport(trickle))
    started = time.monotonic()
    outcome = await transport.post_once(CALLBACK_URL, {}, timeout_seconds=5, total_deadline_seconds=0.2)
    elapsed = time.monotonic() - started
    assert (outcome.kind, classify(outcome)) == ("timeout", "retry")
    assert elapsed < 1.0


@pytest.mark.asyncio
async def test_redirect_and_permanent_failures_stop() -> None:
    for status in (302, 308, 410, 422):
        assert await attempt(status) == ("failed", 1)
    rejected = HttpxCallbackTransport(
        security(allowed_callback_hosts=()), httpx.MockTransport(lambda r: httpx.Response(200))
    )
    outcome = await rejected.post_once(CALLBACK_URL, {}, timeout_seconds=5, total_deadline_seconds=15)
    assert (outcome.kind, classify(outcome)) == ("rejected", "failed")
