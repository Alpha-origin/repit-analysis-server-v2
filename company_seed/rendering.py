"""Isolated Chromium rendering; network requests use the policy-aware HTTP collector."""

from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import httpx

from company_seed.documents import CompanySeed, allowed_url

if TYPE_CHECKING:
    from playwright.async_api import Browser, Route, WebSocketRoute

Fetch = Callable[[str, list[str]], Awaitable[tuple[bytes, httpx.Headers, str]]]
MAX_REQUESTS = 40
MAX_RENDER_SECONDS = 45


def browser_ready() -> bool:
    if importlib.util.find_spec("playwright") is None:
        return False
    from playwright.sync_api import sync_playwright  # noqa: PLC0415 -- optional dependency

    with sync_playwright() as runtime:
        return Path(runtime.chromium.executable_path).is_file()


class RouteGate:
    def __init__(self, company: CompanySeed, fetch: Fetch) -> None:
        self.company = company
        self.fetch = fetch
        self.count = 0
        self.blocked: list[str] = []
        self.pending: set[asyncio.Task[object]] = set()

    async def handle(self, route: Route) -> None:
        task = asyncio.current_task()
        if task:
            self.pending.add(task)
        try:
            await self.dispatch(route)
        finally:
            if task:
                self.pending.discard(task)

    async def block_socket(self, socket: WebSocketRoute) -> None:
        self.blocked.append("WebSocket")
        await socket.close()

    async def dispatch(self, route: Route) -> None:
        request = route.request
        if request.method != "GET" or request.resource_type in {"image", "media", "font", "websocket"}:
            await route.abort()
            return
        self.count += 1
        if self.count > MAX_REQUESTS:
            self.blocked.append("요청 상한")
            await route.abort()
            return
        hosts = (
            self.company.allowed_hosts
            if request.resource_type == "document"
            else [
                *self.company.allowed_hosts,
                *self.company.asset_hosts,
            ]
        )
        try:
            allowed_url(request.url, hosts)
            body, headers, _ = await self.fetch(request.url, hosts)
            clean = {
                key: value
                for key, value in headers.items()
                if key.lower()
                not in {
                    "content-encoding",
                    "content-length",
                    "transfer-encoding",
                    "connection",
                    "set-cookie",
                }
            }
            await route.fulfill(status=200, headers=clean, body=body)
        except (ValueError, httpx.HTTPError):
            self.blocked.append(urlsplit(request.url).hostname or "네트워크")
            await route.abort()

    async def close(self, browser: Browser) -> None:
        from playwright.async_api import Error  # noqa: PLC0415 -- optional dependency

        tasks = [task for task in self.pending if task != asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        with suppress(Error, TimeoutError):
            await asyncio.wait_for(browser.close(), timeout=5)


class BrowserRenderer:
    async def render(self, url: str, company: CompanySeed, fetch: Fetch) -> bytes:
        if importlib.util.find_spec("playwright") is None:
            raise ValueError("JS 본문: uv sync --group company 및 playwright install chromium 필요")
        from playwright.async_api import Error, async_playwright  # noqa: PLC0415 -- optional dependency

        allowed_url(url, company.allowed_hosts)
        gate = RouteGate(company, fetch)
        try:
            async with asyncio.timeout(MAX_RENDER_SECONDS), async_playwright() as runtime:
                browser = await runtime.chromium.launch(headless=True)
                try:
                    context = await browser.new_context(service_workers="block")
                    await context.route("**/*", gate.handle)
                    await context.route_web_socket("**/*", gate.block_socket)
                    page = await context.new_page()
                    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                    await page.wait_for_function(
                        "document.body && document.body.innerText.trim().length >= 100", timeout=8_000
                    )
                    allowed_url(page.url, company.allowed_hosts)
                    text: str = await page.content()
                    if len(text.encode()) > 25_000_000:  # noqa: PLR2004
                        raise ValueError("렌더링 본문 크기 상한 초과")
                    return text.encode()
                finally:
                    await gate.close(browser)
        except (Error, TimeoutError) as exc:
            blocked = ", ".join(sorted(set(gate.blocked))[:5])
            suffix = f", 차단: {blocked}" if blocked else ""
            message = f"JS 렌더링 실패({type(exc).__name__}{suffix}): 출처·assetHosts·브라우저 설치 확인"
            raise ValueError(message) from None
