from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from app.outbound.adapters.httpx_callback_transport import HttpxCallbackTransport
from tests.video.helpers import CALLBACK_HOST, CALLBACK_TOKEN, CALLBACK_URL, SwappableSecurity, security


class Recorder:
    def __init__(self, response: httpx.Response | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.response = response or httpx.Response(204)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.response


@pytest.mark.asyncio
async def test_one_request_with_current_token() -> None:
    recorder = Recorder()
    transport = HttpxCallbackTransport(security(), httpx.MockTransport(recorder))
    outcome = await transport.post_once(CALLBACK_URL, {"jobId": "j"}, timeout_seconds=5)
    assert outcome.kind == "delivered"
    assert outcome.status_code == 204
    assert len(recorder.requests) == 1
    assert recorder.requests[0].headers["x-internal-token"] == CALLBACK_TOKEN


@pytest.mark.asyncio
async def test_revoked_destination_never_receives_token() -> None:
    recorder = Recorder()
    swappable = SwappableSecurity(security())
    transport = HttpxCallbackTransport(swappable, httpx.MockTransport(recorder))
    # "Restart" with the host emergency-blocked: the next send re-checks and sends nothing.
    swappable.provider = security(blocked_callback_hosts=(CALLBACK_HOST,))
    for allow_untrusted in (False, True):
        outcome = await transport.post_once(
            CALLBACK_URL, {"jobId": "j"}, timeout_seconds=5, allow_untrusted=allow_untrusted
        )
        assert outcome.kind == "rejected"
    # Removed from the allow-list: video (strict) sends nothing; legacy dev mode sends without a token.
    swappable.provider = security(allowed_callback_hosts=())
    assert (await transport.post_once(CALLBACK_URL, {}, timeout_seconds=5)).kind == "rejected"
    assert recorder.requests == []
    assert (await transport.post_once(CALLBACK_URL, {}, timeout_seconds=5, allow_untrusted=True)).kind == "delivered"
    assert "x-internal-token" not in recorder.requests[0].headers


@pytest.mark.asyncio
async def test_untrusted_dev_destination_gets_no_token() -> None:
    recorder = Recorder()
    transport = HttpxCallbackTransport(security(), httpx.MockTransport(recorder))
    outcome = await transport.post_once("http://localhost:8080/cb", {}, timeout_seconds=5, allow_untrusted=True)
    assert outcome.kind == "delivered"
    assert "x-internal-token" not in recorder.requests[0].headers
    assert (await transport.post_once("http://localhost:8080/cb", {}, timeout_seconds=5)).kind == "rejected"
    assert len(recorder.requests) == 1


@pytest.mark.asyncio
async def test_redirect_is_not_followed() -> None:
    recorder = Recorder(httpx.Response(307, headers={"Location": "https://evil.example.com/steal"}))
    transport = HttpxCallbackTransport(security(), httpx.MockTransport(recorder))
    outcome = await transport.post_once(CALLBACK_URL, {}, timeout_seconds=5)
    assert outcome.kind == "http_status"
    assert outcome.status_code == 307
    assert [request.url.host for request in recorder.requests] == [CALLBACK_HOST]


@pytest.mark.asyncio
async def test_network_timeout_and_deadline_classification() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    def slow_io(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    async def hanging(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        return httpx.Response(200)

    network = HttpxCallbackTransport(security(), httpx.MockTransport(broken))
    assert (await network.post_once(CALLBACK_URL, {}, timeout_seconds=5)).kind == "network"
    io = HttpxCallbackTransport(security(), httpx.MockTransport(slow_io))
    assert (await io.post_once(CALLBACK_URL, {}, timeout_seconds=5)).kind == "timeout"
    started = time.monotonic()
    deadline = HttpxCallbackTransport(security(), httpx.MockTransport(hanging))
    outcome = await deadline.post_once(CALLBACK_URL, {}, timeout_seconds=30, total_deadline_seconds=0.05)
    assert outcome.kind == "timeout"
    assert outcome.reason == "total_deadline"
    assert time.monotonic() - started < 2
