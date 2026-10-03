from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.core.common.security.dto import AttemptOutcome
from app.core.common.security.ports import RuntimeSecurityProvider
from app.core.common.security.url_policy import UrlPolicyError, check_callback_url, parse_https_url

TOKEN_HEADER = "X-Internal-Token"  # noqa: S105 — header name, not a secret


class HttpxCallbackTransport:
    """Exactly one POST per call, with the destination re-checked against the *current* policy.

    No redirects, no proxy/environment inheritance, TLS verification always on. The response body is
    never read: status and headers are enough, and an arbitrary body could be huge or sensitive.
    """

    def __init__(self, security: RuntimeSecurityProvider, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._security = security
        self._transport = transport

    async def post_once(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        timeout_seconds: float,
        total_deadline_seconds: float | None = None,
        allow_untrusted: bool = False,
    ) -> AttemptOutcome:
        snapshot = self._security.current()
        headers = {"Content-Type": "application/json"}
        try:
            check_callback_url(url, snapshot.allowed_callback_hosts, snapshot.blocked_callback_hosts)
        except UrlPolicyError as exc:
            if exc.reason == "blocked_host" or not allow_untrusted or _is_blocked(url, snapshot.blocked_callback_hosts):
                return AttemptOutcome(kind="rejected", reason=exc.reason)
        else:
            if snapshot.callback_token:
                headers[TOKEN_HEADER] = snapshot.callback_token
        try:
            if total_deadline_seconds is None:
                return await self._send(url, payload, headers, timeout_seconds)
            # HTTPX timeouts are per I/O operation; this bounds connect+write+headers as a whole.
            async with asyncio.timeout(total_deadline_seconds):
                return await self._send(url, payload, headers, timeout_seconds)
        except TimeoutError:
            return AttemptOutcome(kind="timeout", reason="total_deadline")

    async def _send(
        self, url: str, payload: dict[str, Any], headers: dict[str, str], timeout_seconds: float
    ) -> AttemptOutcome:
        try:
            async with (
                httpx.AsyncClient(
                    timeout=timeout_seconds,
                    transport=self._transport,
                    follow_redirects=False,
                    trust_env=False,
                    verify=True,
                ) as client,
                client.stream("POST", url, json=payload, headers=headers) as response,
            ):
                status = response.status_code
        except httpx.TimeoutException:
            return AttemptOutcome(kind="timeout", reason="io_timeout")
        except httpx.HTTPError:
            return AttemptOutcome(kind="network", reason="network_error")
        if 200 <= status < 300:  # noqa: PLR2004
            return AttemptOutcome(kind="delivered", status_code=status)
        return AttemptOutcome(kind="http_status", status_code=status)


def _is_blocked(url: str, blocked: frozenset[str]) -> bool:
    try:
        return parse_https_url(url).host in blocked
    except UrlPolicyError:
        # Unparseable by the strict parser; fall back to urllib's view of the host for blocking.
        try:
            host = httpx.URL(url).host
        except (httpx.InvalidURL, ValueError):
            return True
        return host.lower().rstrip(".") in blocked
