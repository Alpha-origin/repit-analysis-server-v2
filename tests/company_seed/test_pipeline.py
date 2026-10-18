from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import fitz
import httpx
import pytest
from anthropic import AsyncAnthropic
from company_seed.collection import Collector, SourceError, cached_documents
from company_seed.documents import CompanySeed, Document, Seed, allowed_url, parse_document, read_registry
from company_seed.extraction import (
    ClaudeExtractor,
    Evidence,
    Extraction,
    GroundedFact,
    assemble,
    build_input,
    check_evidence,
    extract_cached,
    fingerprint,
)
from company_seed.pipeline import execute, main
from company_seed.schema import Fact

TODAY = date(2026, 10, 18)
TEXT = "주식회사 테스트는 검색과 지도를 제공한다. 사용자 관점을 존중하며 동료와 협업한다. " * 5
HTML = f"<html><title>테스트 회사</title><nav>제외할 메뉴</nav><article><p>{TEXT}</p></article></html>"


@pytest.fixture
def company() -> CompanySeed:
    return CompanySeed(
        code="test-co",
        display_name="테스트",
        category="PLATFORM",
        boundary_note="테스트 법인만",
        allowed_hosts=["example.com"],
        sources=[Seed(url="https://example.com/about", source_type="ABOUT_PAGE", scope="테스트 법인")],
    )


@pytest.fixture
def documents(company: CompanySeed) -> list[Document]:
    return [parse_document(company.sources[0], HTML.encode(), "text/html", TODAY)[0]]


def extraction(documents: list[Document]) -> Extraction:
    doc = documents[0]
    proof = Evidence(
        source_key=doc.source.source_key,
        passage_id=doc.passages[0].passage_id,
        quote="주식회사 테스트는 검색과 지도를 제공한다.",
    )
    return Extraction(
        legal_name="주식회사 테스트",
        legal_evidence=proof,
        facts=[
            GroundedFact(
                fact=Fact.model_validate(
                    {"kind": kind, "title": title, "content": content, "sourceKey": proof.source_key}
                ),
                evidence=proof,
            )
            for kind, title, content in [
                ("BUSINESS", "검색", "검색 서비스를 제공한다."),
                ("BUSINESS", "지도", "지도 서비스를 제공한다."),
                ("TALENT", "협업", "동료와 협업한다."),
            ]
        ],
        interviewer_brief="검색과 지도를 제공하며 협업을 강조한다.",
    )


class FakeExtractor:
    model = "fixture-model"

    def __init__(self) -> None:
        self.calls = 0

    async def extract(self, company: CompanySeed, documents: list[Document]) -> Extraction:
        self.calls += 1
        return extraction(documents)


def test_registry_covers_same_26() -> None:
    root = Path(__file__).resolve().parents[2] / "company_seed"
    companies = read_registry(root / "sources.json")
    assert len(companies) == 26
    assert all(c.sources for c in companies)
    assert sum(s.verified for c in companies for s in c.sources) == 20


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/",
        "https://evil.example/",
        "https://example.com:444/",
        "https://user:pass@example.com/",
        "https://example.com.evil.org/",
    ],
)
def test_allowlist(url: str) -> None:
    with pytest.raises(ValueError, match=r".+"):
        allowed_url(url, ["example.com"])


def test_extract_body_and_reject_shell(company: CompanySeed, documents: list[Document]) -> None:
    assert "제외할 메뉴" not in documents[0].passages[0].text
    with pytest.raises(ValueError, match="본문 부족"):
        parse_document(company.sources[0], b"<html><script>clientOnly()</script></html>", "text/html", TODAY)


def test_pdf_preserves_page(company: CompanySeed) -> None:
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((50, 50), "A documented public company service case. " * 4, fontsize=8)
        body = pdf.tobytes()
    doc, links = parse_document(company.sources[0], body, "application/pdf", TODAY)
    assert doc.passages[0].passage_id == "page-1"
    assert links == []


def test_evidence_and_unknown_legal_name(company: CompanySeed, documents: list[Document]) -> None:
    result = extraction(documents)
    package = assemble(company, documents, result)
    assert package.review.reviewed_at is None
    result.legal_evidence = None
    with pytest.raises(ValueError, match="법인명에는 원문 근거"):
        assemble(company, documents, result)
    forged = Evidence(source_key=documents[0].source.source_key, passage_id="p-1", quote="없는 사실")
    with pytest.raises(ValueError, match="원문에 없는"):
        check_evidence(forged, documents)


def test_legal_mismatch_and_source_mismatch(company: CompanySeed, documents: list[Document]) -> None:
    company.legal_name = "다른 법인"
    with pytest.raises(ValueError, match="등록 법인명"):
        assemble(company, documents, extraction(documents))
    company.legal_name = None
    result = extraction(documents)
    result.facts[0].fact.source_key = "forged"
    with pytest.raises(ValueError, match="sourceKey가 다름"):
        assemble(company, documents, result)


def test_input_budget_and_version(company: CompanySeed, documents: list[Document]) -> None:
    assert build_input(company, documents)["documents"]
    assert fingerprint(company, documents, "v1") != fingerprint(company, documents, "v2")
    changed = documents[0].model_copy(update={"content_hash": "changed"})
    assert fingerprint(company, documents, "v1") != fingerprint(company, [changed], "v1")


async def test_extraction_cache_skips_model(tmp_path: Path, company: CompanySeed, documents: list[Document]) -> None:
    extractor = FakeExtractor()
    first = await extract_cached(company, documents, tmp_path, extractor)
    second = await extract_cached(company, documents, tmp_path, extractor)
    assert first[2] is False
    assert second[2] is True
    assert extractor.calls == 1
    assert first[0] == second[0]


async def test_collection_versions_and_changed_text(tmp_path: Path, company: CompanySeed) -> None:
    body = HTML

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        collector = Collector(client, delay=0)
        first = await collector.collect(company, tmp_path, TODAY)
        second = await collector.collect(company, tmp_path, TODAY)
        assert first["pages"][0]["status"] == "changed"
        assert second["pages"][0]["status"] == "unchanged"
        body = HTML.replace("검색", "검색개선")
        third = await collector.collect(company, tmp_path, TODAY)
    assert third["pages"][0]["status"] == "changed"
    assert len(list((tmp_path / company.code).glob("web-*.json"))) == 2
    assert len(cached_documents(company, tmp_path)) == 1
    historical = cached_documents(company, tmp_path, versions=first["documents"])
    assert historical[0].content_hash != cached_documents(company, tmp_path)[0].content_hash


@pytest.mark.parametrize("mode", ["robots-denied", "robots-error", "redirect", "empty"])
async def test_collection_failures(tmp_path: Path, company: CompanySeed, mode: str) -> None:
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.path == "/robots.txt":
            if mode == "robots-error":
                return httpx.Response(403)
            return httpx.Response(
                200, text="User-agent: *\n" + ("Disallow: /" if mode == "robots-denied" else "Allow: /")
            )
        if mode == "redirect":
            return httpx.Response(302, headers={"location": "https://evil.example/private"})
        return httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await Collector(client, delay=0).collect(company, tmp_path, TODAY)
    assert report["documents"] == []
    assert report["pages"][0]["status"] == "failed"
    assert not any("evil.example" in url for url in requested)
    if mode.startswith("robots"):
        assert len(requested) == 1


async def test_pipeline_e2e_and_no_overwrite(tmp_path: Path, company: CompanySeed) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=HTML, headers={"content-type": "text/html"})

    output = tmp_path / "run"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        collector = Collector(client, delay=0)
        extractor = FakeExtractor()
        assert await execute("run", [company], tmp_path / "cache", output, collector, extractor) == 0
        assert (output / "drafts/test-co.json").is_file()
        package = json.loads((output / "drafts/test-co.json").read_text())
        assert package["review"] == {"reviewedBy": None, "reviewedAt": None}
        with pytest.raises(FileExistsError):
            await execute("run", [company], tmp_path / "cache", output, collector, extractor)


async def test_partial_batch_continues(tmp_path: Path, company: CompanySeed) -> None:
    failed = company.model_copy(update={"code": "failed-co", "legal_name": "일치하지 않는 법인"})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=HTML if request.url.path != "/robots.txt" else "User-agent: *\nAllow: /",
            headers={"content-type": "text/html"},
        )

    output = tmp_path / "run"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        code = await execute(
            "run", [failed, company], tmp_path / "cache", output, Collector(client, delay=0), FakeExtractor()
        )
    assert code == 1
    report = json.loads((output / "report.json").read_text())
    assert [row["status"] for row in report] == ["failed", "draft"]


def test_cli_plan_no_calls_and_unknown_company() -> None:
    assert main(["plan", "--all"]) == 0
    assert main(["plan", "--company", "unknown-company"]) == 1


def test_legal_footer_preserved_but_footer_only_shell_rejected(company: CompanySeed) -> None:
    body = HTML + "<footer><p>주식회사 테스트</p><p>사업자번호 1234567890</p></footer>"
    doc, _ = parse_document(company.sources[0], body.encode(), "text/html", TODAY)
    assert any(p.passage_id.startswith("footer-") for p in doc.passages)
    shell = "<html><script>render()</script><footer>" + TEXT + "</footer></html>"
    with pytest.raises(ValueError, match="본문 부족"):
        parse_document(company.sources[0], shell.encode(), "text/html", TODAY)


async def test_redirect_target_robots_checked(tmp_path: Path, company: CompanySeed) -> None:
    visited = []

    def handler(request: httpx.Request) -> httpx.Response:
        visited.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private")
        return httpx.Response(302, headers={"location": "/private"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await Collector(client, delay=0).collect(company, tmp_path, TODAY)
    assert "/private" not in visited
    assert report["pages"][0]["status"] == "failed"


async def test_retry_and_byte_cap(tmp_path: Path, company: CompanySeed, monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    async def immediate_sleep(seconds: float) -> None:
        pass

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        attempts += 1
        return (
            httpx.Response(503)
            if attempts < 3
            else httpx.Response(200, text=HTML, headers={"content-type": "text/html"})
        )

    monkeypatch.setattr("company_seed.collection.asyncio.sleep", immediate_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        collector = Collector(client, delay=0)
        report = await collector.collect(company, tmp_path, TODAY)
        assert len(report["documents"]) == 1
        assert attempts == 3
        with pytest.raises(ValueError, match="문서 크기"):
            await collector._get(company.sources[0].url, company.allowed_hosts, cap=5)


async def test_missing_api_key_fails_before_provider_call(
    company: CompanySeed, documents: list[Document], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        await ClaudeExtractor("fixture").extract(company, documents)


async def test_sdk_adapter_with_mock_http(
    company: CompanySeed, documents: list[Document], monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = extraction(documents).model_dump(by_alias=True, mode="json")
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "msg_fixture",
                "type": "message",
                "role": "assistant",
                "model": "fixture",
                "content": [
                    {"type": "tool_use", "id": "tool_fixture", "name": "submit_company_extraction", "input": payload}
                ],
                "stop_reason": "tool_use",
                "stop_sequence": None,
                "usage": {"input_tokens": 42, "output_tokens": 84},
            },
        )

    def factory(*, timeout: float, max_retries: int) -> AsyncAnthropic:
        return AsyncAnthropic(
            api_key="fixture-not-real",
            max_retries=max_retries,
            timeout=timeout,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture-not-real")
    monkeypatch.setattr("company_seed.extraction.AsyncAnthropic", factory)
    extractor = ClaudeExtractor("fixture")
    assert await extractor.extract(company, documents) == extraction(documents)
    assert sent[0]["tool_choice"] == {"type": "tool", "name": "submit_company_extraction"}
    assert extractor.usage == {"inputTokens": 42, "outputTokens": 84}


def test_metadata_change_also_changes_version(company: CompanySeed) -> None:
    first, _ = parse_document(company.sources[0], HTML.encode(), "text/html", TODAY)
    second, _ = parse_document(
        company.sources[0], HTML.replace("테스트 회사", "수정 제목").encode(), "text/html", TODAY
    )
    assert first.content_hash != second.content_hash


async def test_no_model_or_export_for_bad_extraction(
    tmp_path: Path, company: CompanySeed, documents: list[Document]
) -> None:
    class ForgedExtractor(FakeExtractor):
        async def extract(self, company: CompanySeed, documents: list[Document]) -> Extraction:
            result = extraction(documents)
            result.facts[0].evidence.quote = "원문에 없는 가짜 근거"
            return result

    with pytest.raises(ValueError, match="원문에 없는"):
        await extract_cached(company, documents, tmp_path, ForgedExtractor())
    assert not list((tmp_path / company.code / "extractions").glob("*.json"))


def _page_fetcher(statuses: list[httpx.Response]) -> tuple[list[float], httpx.MockTransport]:
    sleeps: list[float] = []
    responses = iter(statuses)

    def handler(request: httpx.Request) -> httpx.Response:
        return next(responses)

    return sleeps, httpx.MockTransport(handler)


async def test_retry_waits_for_host_crawl_delay(company: CompanySeed, monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps, transport = _page_fetcher(
        [httpx.Response(503), httpx.Response(200, text=HTML, headers={"content-type": "text/html"})]
    )

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("company_seed.collection.asyncio.sleep", record_sleep)
    url = company.sources[0].url
    async with httpx.AsyncClient(transport=transport) as client:
        collector = Collector(client, delay=0)
        collector.host_delay[httpx.URL(url).host] = 10
        await collector._get(url, company.allowed_hosts)
    assert len(sleeps) == 2
    assert sleeps[1] >= 9.5  # 재시도도 robots 간격(10초)을 지킨다


async def test_retry_honors_retry_after(company: CompanySeed, monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps, transport = _page_fetcher(
        [
            httpx.Response(429, headers={"retry-after": "30"}),
            httpx.Response(200, text=HTML, headers={"content-type": "text/html"}),
        ]
    )

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("company_seed.collection.asyncio.sleep", record_sleep)
    async with httpx.AsyncClient(transport=transport) as client:
        await Collector(client, delay=0)._get(company.sources[0].url, company.allowed_hosts)
    assert sleeps[1] >= 29.5


async def test_retry_after_beyond_policy_limit_stops(company: CompanySeed, monkeypatch: pytest.MonkeyPatch) -> None:
    _, transport = _page_fetcher([httpx.Response(503, headers={"retry-after": "3600"})])

    async def immediate_sleep(seconds: float) -> None:
        pass

    monkeypatch.setattr("company_seed.collection.asyncio.sleep", immediate_sleep)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(SourceError, match="Retry-After"):
            await Collector(client, delay=0)._get(company.sources[0].url, company.allowed_hosts)
