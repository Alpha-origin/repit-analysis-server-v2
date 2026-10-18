"""Bounded same-registry HTTP collection with robots and durable content versions."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import NotRequired, TypedDict
from urllib.parse import urljoin, urlsplit

import fitz
import httpx
from protego import Protego

from company_seed.discovery import locations
from company_seed.documents import CompanySeed, Document, Seed, allowed_url, parse_document
from company_seed.rendering import BrowserRenderer
from company_seed.storage import write_json, write_text

USER_AGENT = "RepitCompanyDataset/1.0"
MAX_BYTES = 25_000_000
MAX_PAGES = 12
MAX_POLICY_DELAY = 60
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 3
LINK_HINTS = (
    "culture",
    "career",
    "recruit",
    "talent",
    "value",
    "service",
    "product",
    "about",
    "business",
    "ideal",
    "feed",
    "rss",
    "sitemap",
)


class PageResult(TypedDict):
    url: str
    status: str
    version: NotRequired[str]
    error: NotRequired[str]
    errorCode: NotRequired[str]
    method: NotRequired[str]


class CollectionReport(TypedDict):
    company: str
    checkedAt: str
    documents: list[str]
    pages: list[PageResult]
    limitReached: bool
    inProgress: NotRequired[bool]


class SourceError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Collector:
    def __init__(
        self, client: httpx.AsyncClient, *, delay: float = 1.0, renderer: BrowserRenderer | None = None
    ) -> None:
        self.client = client
        self.renderer = renderer
        self.host_locks: dict[str, asyncio.Lock] = {}
        self.robot_locks: dict[str, asyncio.Lock] = {}
        self.policy_errors: dict[str, str] = {}
        self.sitemaps: dict[str, list[str]] = {}
        self.delay = delay
        self.last_request: dict[str, float] = {}
        self.robots: dict[str, Protego] = {}
        self.host_delay: dict[str, float] = {}

    async def _get(
        self,
        url: str,
        hosts: list[str],
        *,
        cap: int = MAX_BYTES,
        check_robots: bool = True,
    ) -> tuple[bytes, str, str]:
        body, headers, final = await self._get_response(url, hosts, cap=cap, check_robots=check_robots)
        return body, headers.get("content-type", ""), final

    async def _get_response(
        self,
        url: str,
        hosts: list[str],
        *,
        cap: int = MAX_BYTES,
        check_robots: bool = True,
    ) -> tuple[bytes, httpx.Headers, str]:
        for _ in range(4):
            url = allowed_url(url, hosts)
            host = urlsplit(url).netloc
            body, headers, status = await self._fetch_with_retry(url, host, cap)
            if status in {301, 302, 303, 307, 308}:
                location = headers.get("location")
                if not location:
                    raise SourceError("REDIRECT", "리디렉션 위치 없음")
                url = allowed_url(urljoin(url, location), hosts)
                if check_robots:
                    await self.require_allowed(url, hosts)
                continue
            if status >= 400:  # noqa: PLR2004
                raise SourceError(f"HTTP_{status}", f"HTTP {status}")
            return body, headers, url
        raise SourceError("REDIRECT", "리디렉션 횟수 초과")

    async def render_resource(self, url: str, hosts: list[str]) -> tuple[bytes, httpx.Headers, str]:
        await self.require_allowed(url, hosts)
        return await self._get_response(url, hosts)

    async def require_allowed(self, url: str, hosts: list[str]) -> None:
        if not await self.allowed(url, hosts):
            origin = "https://" + urlsplit(url).netloc
            code = self.policy_errors.get(origin, "ROBOTS_DENIED")
            raise SourceError(code, f"{code}: robots 정책에 따라 수집하지 않음")

    async def _fetch_with_retry(self, url: str, host: str, cap: int) -> tuple[bytes, httpx.Headers, int]:
        # 재시도도 매번 호스트 잠금과 robots 간격을 거친다. 백오프·Retry-After가 더 길면 그만큼 더 기다린다.
        backoff = 0.0
        for attempt in range(MAX_ATTEMPTS):
            last = attempt == MAX_ATTEMPTS - 1
            async with self.host_locks.setdefault(host, asyncio.Lock()):
                gap = max(self.host_delay.get(host, self.delay), backoff)
                await asyncio.sleep(max(0, gap - (time.monotonic() - self.last_request.get(host, 0))))
                self.last_request[host] = time.monotonic()
                try:
                    body, headers, status = await self._request(url, cap, read_body=last)
                except httpx.TransportError:
                    if last:
                        raise SourceError("NETWORK", "네트워크 연결 실패") from None
                    backoff = float(2**attempt)
                    continue
            if status not in RETRY_STATUSES or last:
                return body, headers, status
            retry_after = _retry_after_seconds(headers.get("retry-after"))
            if retry_after is not None and retry_after > MAX_POLICY_DELAY:
                raise SourceError(f"HTTP_{status}", f"HTTP {status}: Retry-After {retry_after:.0f}초")
            backoff = max(float(2**attempt), retry_after or 0.0)
        raise ValueError("재시도 소진")

    async def _request(self, url: str, cap: int, *, read_body: bool = True) -> tuple[bytes, httpx.Headers, int]:
        async with self.client.stream("GET", url, headers={"User-Agent": USER_AGENT}) as response:
            if response.status_code in RETRY_STATUSES and not read_body:
                return b"", response.headers, response.status_code
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > cap:
                    raise SourceError("SIZE_LIMIT", "문서 크기 상한 초과")
            return bytes(data), response.headers, response.status_code

    async def allowed(self, url: str, hosts: list[str]) -> bool:
        parsed = urlsplit(allowed_url(url, hosts))
        origin = f"https://{parsed.netloc}"
        async with self.robot_locks.setdefault(origin, asyncio.Lock()):
            if origin not in self.robots:
                await self._load_policy(origin, parsed.netloc, hosts)
        if self.host_delay[parsed.netloc] > MAX_POLICY_DELAY:
            self.policy_errors[origin] = "ROBOTS_DELAY"
            return False
        return self.robots[origin].can_fetch(url, USER_AGENT)

    async def _load_policy(self, origin: str, host: str, hosts: list[str]) -> None:
        try:
            body, _, _ = await self._get(origin + "/robots.txt", hosts, cap=500_000, check_robots=False)
            text = body.decode("utf-8", errors="replace")
        except ValueError as exc:
            if str(exc) == "HTTP 404":
                text = "User-agent: *\nAllow: /"
            else:
                text = "User-agent: *\nDisallow: /"
                self.policy_errors[origin] = "ROBOTS_UNAVAILABLE"
        policy = Protego.parse(text)
        delay = policy.crawl_delay(USER_AGENT) or 0
        rate = policy.request_rate(USER_AGENT)
        rate_delay = float(rate.seconds) / float(rate.requests) if rate and float(rate.requests) > 0 else 0.0
        self.host_delay[host] = max(self.delay, float(delay), rate_delay)
        # Unimplemented time-window extensions fail closed instead of guessing their meaning.
        if policy.visit_time(USER_AGENT) or (rate and rate.start_time is not None):
            self.policy_errors[origin] = "ROBOTS_TIME_WINDOW"
            policy = Protego.parse("User-agent: *\nDisallow: /")
        self.robots[origin] = policy
        self.sitemaps[origin] = list(policy.sitemaps)

    async def collect(self, company: CompanySeed, cache: Path, today: date) -> CollectionReport:
        root = cache / company.code
        root.mkdir(parents=True, exist_ok=True)
        queue = list(company.sources)
        sitemap_origins: set[str] = set()
        seen: set[str] = set()
        rows: list[PageResult] = []
        manual_index = root / "manual-index.json"
        documents: list[str] = json.loads(manual_index.read_text(encoding="utf-8")) if manual_index.exists() else []
        initial: CollectionReport = {
            "company": company.code,
            "checkedAt": today.isoformat(),
            "documents": list(documents),
            "pages": [],
            "limitReached": False,
            "inProgress": True,
        }
        write_json(root / "latest.json", initial)
        while queue and len(seen) < MAX_PAGES:
            seed = queue.pop(0)
            if seed.url in seen:
                continue
            seen.add(seed.url)
            try:
                document, links, discovery = await self.read_seed(seed, company, today)
                if document:
                    version = store_document(root, document)
                    changed = version[1]
                    documents.append(version[0])
                    rows.append(
                        {
                            "url": seed.url,
                            "status": "changed" if changed else "unchanged",
                            "version": version[0],
                            "method": document.acquisition,
                        }
                    )
                else:
                    rows.append({"url": seed.url, "status": "discovered", "method": "xml"})
                origin = "https://" + urlsplit(seed.url).netloc
                if origin not in sitemap_origins:
                    for sitemap in self.sitemaps.get(origin, []):
                        try:
                            clean = allowed_url(sitemap, company.allowed_hosts)
                        except ValueError:
                            continue
                        queue.append(Seed(url=clean, source_type=seed.source_type, scope=seed.scope, discovery=True))
                    sitemap_origins.add(origin)
                queued = {item.url for item in queue} | seen
                queue.extend(
                    item for item in discover(links, company, seed, all_links=discovery) if item.url not in queued
                )
            except (ValueError, RuntimeError, fitz.FileDataError) as exc:
                rows.append(
                    {
                        "url": seed.url,
                        "status": "failed",
                        "error": str(exc),
                        "errorCode": exc.code if isinstance(exc, SourceError) else "BODY_OR_HOST",
                    }
                )
            progress: CollectionReport = {
                "company": company.code,
                "checkedAt": today.isoformat(),
                "documents": list(dict.fromkeys(documents)),
                "pages": rows,
                "limitReached": bool(queue),
                "inProgress": True,
            }
            write_json(root / "latest.json", progress)
        manual = root / "manual-index.json"
        if manual.exists():
            documents = [*json.loads(manual.read_text(encoding="utf-8")), *documents]
        report: CollectionReport = {
            "company": company.code,
            "checkedAt": today.isoformat(),
            "documents": list(dict.fromkeys(documents)),
            "pages": rows,
            "limitReached": bool(queue),
            "inProgress": False,
        }
        write_json(root / "latest.json", report)
        return report

    async def read_seed(
        self,
        seed: Seed,
        company: CompanySeed,
        today: date,
    ) -> tuple[Document | None, list[str], bool]:
        await self.require_allowed(seed.url, company.allowed_hosts)
        body, content_type, final_url = await self._get(seed.url, company.allowed_hosts)
        resolved = seed.model_copy(update={"url": final_url})
        if seed.discovery or "xml" in content_type or body.lstrip().startswith(b"<?xml"):
            links, _ = locations(body, final_url)
            return None, links, True
        try:
            document, links = parse_document(resolved, body, content_type, today)
        except ValueError as exc:
            if "본문 부족" not in str(exc) or self.renderer is None or "html" not in content_type:
                raise
            rendered = await self.renderer.render(final_url, company, self.render_resource)
            document, links = parse_document(resolved, rendered, "text/html", today)
            document.acquisition = "browser"
        return document, links, False


def store_document(root: Path, document: Document) -> tuple[str, bool]:
    version = f"{document.source.source_key}-{document.acquisition}-{document.content_hash}.json"
    target = root / version
    changed = not target.exists()
    if changed:
        write_text(target, document.model_dump_json(by_alias=True, indent=2))
    return version, changed


def discover(links: list[str], company: CompanySeed, parent: Seed, *, all_links: bool = False) -> list[Seed]:
    result = []
    for link in links:
        try:
            url = allowed_url(link, company.allowed_hosts)
        except ValueError:
            continue
        if not all_links and (
            urlsplit(url).query or not any(hint in urlsplit(url).path.lower() for hint in LINK_HINTS)
        ):
            continue
        result.append(
            Seed(
                url=url,
                source_type=parent.source_type,
                scope=parent.scope,
                verified=False,
                discovery=urlsplit(url).path.lower().endswith(".xml"),
            )
        )
    return result[:MAX_PAGES]


def cached_documents(company: CompanySeed, cache: Path, *, versions: list[str] | None = None) -> list[Document]:
    root = cache / company.code
    if versions is None:
        report = json.loads((root / "latest.json").read_text(encoding="utf-8"))
        versions = report["documents"]
    documents: dict[str, Document] = {}
    for name in versions or []:
        # Only versions referenced by this latest collection belong to this run.
        if Path(name).name != name:
            raise ValueError("잘못된 캐시 파일 이름")
        document = Document.model_validate_json((root / name).read_text(encoding="utf-8"))
        allowed_url(document.source.url, company.allowed_hosts)
        documents[document.source.source_key] = document
    return list(documents.values())


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())
