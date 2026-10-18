"""Bounded same-registry HTTP collection with robots and durable content versions."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import date
from pathlib import Path
from typing import NotRequired, TypedDict
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import fitz
import httpx

from company_seed.documents import CompanySeed, Document, Seed, allowed_url, parse_document

USER_AGENT = "RepitCompanyDataset/1.0"
MAX_BYTES = 25_000_000
MAX_PAGES = 12
MAX_POLICY_DELAY = 60
LINK_HINTS = ("culture", "career", "recruit", "talent", "value", "service", "product", "about", "business", "ideal")


class PageResult(TypedDict):
    url: str
    status: str
    version: NotRequired[str]
    error: NotRequired[str]


class CollectionReport(TypedDict):
    company: str
    checkedAt: str
    documents: list[str]
    pages: list[PageResult]
    limitReached: bool


class Collector:
    def __init__(self, client: httpx.AsyncClient, *, delay: float = 1.0) -> None:
        self.client = client
        self.delay = delay
        self.last_request: dict[str, float] = {}
        self.robots: dict[str, RobotFileParser] = {}
        self.host_delay: dict[str, float] = {}

    async def _get(
        self, url: str, hosts: list[str], *, cap: int = MAX_BYTES, check_robots: bool = True
    ) -> tuple[bytes, str, str]:
        for _ in range(4):
            url = allowed_url(url, hosts)
            host = urlsplit(url).netloc
            await asyncio.sleep(
                max(0, self.host_delay.get(host, self.delay) - (time.monotonic() - self.last_request.get(host, 0)))
            )
            self.last_request[host] = time.monotonic()
            body, headers, status = await self._request(url, cap)
            if status in {301, 302, 303, 307, 308}:
                location = headers.get("location")
                if not location:
                    raise ValueError("리디렉션 위치 없음")
                url = allowed_url(urljoin(url, location), hosts)
                # Each redirected page has its own robots policy.
                if check_robots and not await self.allowed(url, hosts):
                    raise ValueError("리디렉션 대상 robots 차단")
                continue
            if status >= 400:  # noqa: PLR2004
                raise ValueError(f"HTTP {status}")
            return body, headers.get("content-type", ""), url
        raise ValueError("리디렉션 횟수 초과")

    async def _request(self, url: str, cap: int) -> tuple[bytes, httpx.Headers, int]:
        for attempt in range(3):
            try:
                async with self.client.stream("GET", url, headers={"User-Agent": USER_AGENT}) as response:
                    if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:  # noqa: PLR2004
                        await asyncio.sleep(2**attempt)
                        continue
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > cap:
                            raise ValueError("문서 크기 상한 초과")
                    return bytes(data), response.headers, response.status_code
            except httpx.TransportError:
                if attempt == 2:  # noqa: PLR2004
                    raise ValueError("네트워크 연결 실패") from None
                await asyncio.sleep(2**attempt)
        raise ValueError("재시도 소진")

    async def allowed(self, url: str, hosts: list[str]) -> bool:
        parsed = urlsplit(allowed_url(url, hosts))
        origin = f"https://{parsed.netloc}"
        if origin not in self.robots:
            parser = RobotFileParser(origin + "/robots.txt")
            try:
                body, _, _ = await self._get(origin + "/robots.txt", hosts, cap=500_000, check_robots=False)
                parser.parse(body.decode("utf-8", errors="replace").splitlines())
            except ValueError as exc:
                if str(exc) == "HTTP 404":
                    parser.parse(["User-agent: *", "Allow: /"])
                else:
                    # Fail closed on inaccessible robots, including auth and server errors.
                    parser.parse(["User-agent: *", "Disallow: /"])
            delay = parser.crawl_delay(USER_AGENT) or parser.crawl_delay("*") or 0
            rate = parser.request_rate(USER_AGENT) or parser.request_rate("*")
            rate_delay = float(rate.seconds) / float(rate.requests) if rate and float(rate.requests) > 0 else 0.0
            self.host_delay[parsed.netloc] = max(self.delay, float(delay), rate_delay)
            self.robots[origin] = parser
        return self.host_delay[parsed.netloc] <= MAX_POLICY_DELAY and self.robots[origin].can_fetch(USER_AGENT, url)

    async def collect(self, company: CompanySeed, cache: Path, today: date) -> CollectionReport:
        root = cache / company.code
        root.mkdir(parents=True, exist_ok=True)
        queue = list(company.sources)
        seen: set[str] = set()
        rows: list[PageResult] = []
        documents: list[str] = []
        while queue and len(seen) < MAX_PAGES:
            seed = queue.pop(0)
            try:
                url = allowed_url(seed.url, company.allowed_hosts)
                if url in seen:
                    continue
                seen.add(url)
                if not await self.allowed(url, company.allowed_hosts):
                    raise ValueError("robots 차단 또는 정책 조회 실패")
                body, content_type, final_url = await self._get(url, company.allowed_hosts)
                resolved = seed.model_copy(update={"url": final_url})
                document, links = parse_document(resolved, body, content_type, today)
                version = f"{document.source.source_key}-{document.content_hash}.json"
                target = root / version
                changed = not target.exists()
                if changed:
                    target.write_text(document.model_dump_json(by_alias=True, indent=2), encoding="utf-8")
                documents.append(version)
                rows.append({"url": url, "status": "changed" if changed else "unchanged", "version": version})
                queue.extend(discover(links, company, seed))
            except (ValueError, RuntimeError, fitz.FileDataError) as exc:
                rows.append({"url": seed.url, "status": "failed", "error": str(exc)})
        report: CollectionReport = {
            "company": company.code,
            "checkedAt": today.isoformat(),
            "documents": list(dict.fromkeys(documents)),
            "pages": rows,
            "limitReached": bool(queue),
        }
        (root / "latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report


def discover(links: list[str], company: CompanySeed, parent: Seed) -> list[Seed]:
    result = []
    for link in links:
        try:
            url = allowed_url(link, company.allowed_hosts)
        except ValueError:
            continue
        if urlsplit(url).query or not any(hint in urlsplit(url).path.lower() for hint in LINK_HINTS):
            continue
        result.append(Seed(url=url, source_type=parent.source_type, scope=parent.scope, verified=False))
    return result[:MAX_PAGES]


def cached_documents(company: CompanySeed, cache: Path) -> list[Document]:
    root = cache / company.code
    report = json.loads((root / "latest.json").read_text(encoding="utf-8"))
    documents: dict[str, Document] = {}
    for name in report["documents"]:
        # Only versions referenced by this latest collection belong to this run.
        if Path(name).name != name:
            raise ValueError("잘못된 캐시 파일 이름")
        document = Document.model_validate_json((root / name).read_text(encoding="utf-8"))
        allowed_url(document.source.url, company.allowed_hosts)
        documents[document.source.source_key] = document
    return list(documents.values())
