from __future__ import annotations

import asyncio
import io
import json
import zipfile
from datetime import date
from pathlib import Path

import httpx
import pytest
from company_seed.collection import CollectionReport, Collector, cached_documents
from company_seed.dart import DartClient, DartIdentity, enrich, exact_matches
from company_seed.demo import COMPANY, DemoCollector, DemoExtractor, handler, run_demo
from company_seed.discovery import locations
from company_seed.documents import CompanySeed, Seed, parse_document
from company_seed.extraction import MAX_INPUT_CHARS, ClaudeExtractor, assemble, build_input, fingerprint
from company_seed.operations import approve, doctor, import_evidence
from company_seed.pipeline import execute
from company_seed.rendering import BrowserRenderer
from company_seed.schema import Package, export_bundle

TODAY = date(2026, 10, 18)


@pytest.fixture
def company() -> CompanySeed:
    return CompanySeed(
        code="test-company",
        display_name="테스트",
        legal_name="주식회사 테스트",
        category="PLATFORM",
        boundary_note="테스트 법인",
        allowed_hosts=["example.com"],
        sources=[Seed(url="https://example.com/about", scope="테스트 법인", source_type="ABOUT_PAGE")],
    )


def test_xml_discovery_and_entities() -> None:
    urls, index = locations(
        b"<rss><channel><item><link>/article/1</link></item></channel></rss>", "https://example.com/feed"
    )
    assert urls == ["https://example.com/article/1"]
    assert index is False
    urls, index = locations(
        b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>https://example.com/urls.xml</loc></sitemap></sitemapindex>',
        "https://example.com/sitemap.xml",
    )
    assert urls == ["https://example.com/urls.xml"]
    assert index is True
    with pytest.raises(ValueError, match="entity"):
        locations(b'<!DOCTYPE a [<!ENTITY x "bad">]><rss/>', "https://example.com/feed")


async def test_feed_reads_article_not_summary(company: CompanySeed, tmp_path: Path) -> None:
    company.sources = [
        Seed(url="https://example.com/feed.xml", source_type="TECH_BLOG", scope="테스트 기술 글", discovery=True)
    ]

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/feed.xml":
            return httpx.Response(
                200,
                text=(
                    "<rss><channel><item><link>/post-1</link>"
                    "<description>목록 요약</description></item></channel></rss>"
                ),
                headers={"content-type": "text/xml"},
            )
        return httpx.Response(
            200,
            text="<article><p>" + "본문에서 확인한 실제 사례의 범위를 기록한다. " * 8 + "</p></article>",
            headers={"content-type": "text/html"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        report = await Collector(client, delay=0).collect(company, tmp_path, TODAY)
    assert len(report["documents"]) == 1
    docs = cached_documents(company, tmp_path)
    assert docs[0].source.url.endswith("/post-1")
    assert all("목록 요약" not in p.text for p in docs[0].passages)


def test_exact_matching_never_fuzzy(company: CompanySeed) -> None:
    rows = [{"corp_code": "12345678", "corp_name": "테스트"}]
    assert len(exact_matches(rows, company)) == 1
    assert len(exact_matches(rows * 2, company)) == 2
    company.legal_name = "테스트서비스"
    assert exact_matches(rows, company) == []


def directory_zip() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "CORPCODE.xml", "<result><list><corp_code>12345678</corp_code><corp_name>테스트</corp_name></list></result>"
        )
    return output.getvalue()


async def test_dart_cache_and_no_key_leak(company: CompanySeed, tmp_path: Path) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("corpCode.xml"):
            return httpx.Response(200, content=directory_zip())
        return httpx.Response(
            200, json={"status": "000", "corp_code": "12345678", "corp_name": "테스트", "bizr_no": "1234567890"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        first = await DartClient(client, "SENSITIVE_FIXTURE_KEY", tmp_path).resolve(company, TODAY)
        second = await DartClient(client, "SENSITIVE_FIXTURE_KEY", tmp_path).resolve(company, TODAY)
    assert first[0] == second[0]
    assert len(calls) == 2
    assert all("SENSITIVE_FIXTURE_KEY" not in file.read_text() for file in tmp_path.glob("*.json"))


async def test_dart_ambiguity_is_unresolved(company: CompanySeed, tmp_path: Path) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as client:
        dart = DartClient(client, "fixture", tmp_path)
        dart.directory = [
            {"corp_code": "12345678", "corp_name": "테스트"},
            {"corp_code": "87654321", "corp_name": "주식회사 테스트"},
        ]
        assert await dart.resolve(company, TODAY) == (None, "ambiguous")


async def test_dart_network_error_masks_secret(company: CompanySeed, tmp_path: Path) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret URL includes token", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ValueError, match="키 값과 요청 URL은 기록하지 않음"):
            await DartClient(client, "SENSITIVE_FIXTURE_KEY", tmp_path).resolve(company, TODAY)


def test_manual_import_scoped_and_tracked(company: CompanySeed, tmp_path: Path) -> None:
    file = tmp_path / "body.txt"
    file.write_text("주식회사 테스트는 검색과 지도 서비스에 대해 설명한다. 협업할 때 근거와 판단을 공유한다. " * 5)
    import_evidence(company, tmp_path / "cache", file, company.sources[0].url, "테스트 법인", TODAY)
    documents = cached_documents(company, tmp_path / "cache")
    assert documents[0].acquisition == "manual"
    with pytest.raises(ValueError, match="호스트 밖"):
        import_evidence(company, tmp_path / "cache", file, "https://evil.example/", "다른 법인", TODAY)


async def test_demo_is_sample_and_not_exportable(tmp_path: Path) -> None:
    output = tmp_path / "demo"
    assert await run_demo(output) == 0
    package = Package.model_validate_json((output / "drafts/sample-pipeline.json").read_text())
    assert all(source.source_type == "SAMPLE" for source in package.sources)
    assert package.review.reviewed_by is None
    report = json.loads((output / "report.json").read_text())
    assert report[0]["mode"] == "SAMPLE_DEMO"
    assert report[0]["modelRequestsStarted"] == 0
    with pytest.raises(ValueError, match="SAMPLE"):
        export_bundle(output / "drafts", tmp_path / "export", today=TODAY)
    with pytest.raises(ValueError, match="SAMPLE"):
        approve(
            output / "drafts/sample-pipeline.json", tmp_path / "reviewed", "테스트 검수자", confirmed=True, today=TODAY
        )
    assert await run_demo(output, resume=True) == 0


async def test_resume_failed_work_and_manifest_conflict(tmp_path: Path) -> None:
    class TemporaryFailure(DemoExtractor):
        fail = True

        async def extract(self, company, documents):  # type: ignore[no-untyped-def]
            if self.fail:
                raise ValueError("temporary failure")
            return await super().extract(company, documents)

    output = tmp_path / "run"
    extractor = TemporaryFailure()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        collector = DemoCollector(client, delay=0)
        assert await execute("run", [COMPANY], output / "cache", output, collector, extractor, demo=True) == 1
        extractor.fail = False
        assert (
            await execute("run", [COMPANY], output / "cache", output, collector, extractor, demo=True, resume=True) == 0
        )
        with pytest.raises(ValueError, match="재개 설정 불일치"):
            await execute("extract", [COMPANY], output / "cache", output, collector, extractor, demo=True, resume=True)


def test_doctor_does_not_expose_env(company: CompanySeed, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "SENSITIVE_FIXTURE_KEY")
    result = doctor([company], "fixture")
    assert result["anthropicKeyPresent"] is True
    assert "SENSITIVE_FIXTURE_KEY" not in json.dumps(result)


async def test_model_budget_precedes_network(company: CompanySeed, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture")
    with pytest.raises(ValueError, match="호출 상한"):
        await ClaudeExtractor("fixture", max_calls=0).extract(company, [])


async def test_real_chromium_client_rendering(company: CompanySeed, tmp_path: Path) -> None:
    pytest.importorskip("playwright")
    seen = []
    text = "공개 서비스의 본문을 브라우저에서 자바스크립트 실행 후 확인한다. " * 6

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/app.js":
            return httpx.Response(
                200,
                text="document.body.innerHTML = " + json.dumps("<article><p>" + text + "</p></article>"),
                headers={"content-type": "application/javascript"},
            )
        return httpx.Response(
            200, text='<html><body><script src="/app.js"></script></body></html>', headers={"content-type": "text/html"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        collector = Collector(client, delay=0, renderer=BrowserRenderer())
        report = await collector.collect(company, tmp_path, TODAY)
    assert report["pages"][0]["method"] == "browser"
    assert "/app.js" in seen
    assert cached_documents(company, tmp_path)[0].acquisition == "browser"


async def test_http_forbidden_never_uses_browser(company: CompanySeed, tmp_path: Path) -> None:
    class ForbiddenRenderer(BrowserRenderer):
        async def render(self, url, company, fetch):  # type: ignore[no-untyped-def]
            pytest.fail("403 must not attempt browser fallback")

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404 if request.url.path == "/robots.txt" else 403)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        report = await Collector(client, delay=0, renderer=ForbiddenRenderer()).collect(company, tmp_path, TODAY)
    assert report["pages"][0]["errorCode"] == "HTTP_403"


async def test_robot_wildcard_and_longest_allow(company: CompanySeed, tmp_path: Path) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="User-agent: *\nDisallow: /private/*\nAllow: /private/public$")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        collector = Collector(client, delay=0)
        assert not await collector.allowed("https://example.com/private/a", company.allowed_hosts)
        assert await collector.allowed("https://example.com/private/public", company.allowed_hosts)


async def test_browser_rejects_external_and_post(company: CompanySeed, tmp_path: Path) -> None:
    pytest.importorskip("playwright")
    requested = []
    text = "이 자료는 브라우저 경로의 도메인 및 HTTP 메서드 제한을 검증한다. " * 5

    def respond(request: httpx.Request) -> httpx.Response:
        requested.append((request.method, str(request.url)))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        script = (
            'fetch("https://evil.example/private").catch(()=>{});'
            'fetch("/write", {method:"POST"}).catch(()=>{});'
            "document.body.innerHTML=" + json.dumps("<p>" + text + "</p>")
        )
        return httpx.Response(
            200, text="<html><body><script>" + script + "</script></body></html>", headers={"content-type": "text/html"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        report = await Collector(client, delay=0, renderer=BrowserRenderer()).collect(company, tmp_path, TODAY)
    assert report["documents"]
    assert all(method == "GET" and "evil.example" not in url and "/write" not in url for method, url in requested)


def test_explicit_review_declaration(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[2] / "company_seed/data/drafts/naver.json"
    with pytest.raises(ValueError, match="실제 검수자"):
        approve(source, tmp_path / "denied", "테스트용 검수자", confirmed=False, today=TODAY)
    # Synthetic reviewer in a temporary test directory only, never committed real company review records.
    approve(source, tmp_path / "reviewed", "테스트용 검수자", confirmed=True, today=TODAY)
    assert Package.model_validate_json((tmp_path / "reviewed/naver.json").read_text()).review.reviewed_at == TODAY
    with pytest.raises(ValueError, match="덮지 않음"):
        approve(source, tmp_path / "reviewed", "테스트용 검수자", confirmed=True, today=TODAY)


def test_block_page_not_company_body(company: CompanySeed) -> None:
    with pytest.raises(ValueError, match="ACCESS_CHALLENGE"):
        parse_document(
            company.sources[0],
            b"<html><title>Just a moment...</title><body>" + b"challenge " * 50 + b"</body></html>",
            "text/html",
            TODAY,
        )


def test_long_body_and_balanced_input(company: CompanySeed) -> None:
    body = "<article><p>" + ("길게 작성된 회사 서비스 설명. " * 5000) + "</p></article><footer>주식회사 테스트</footer>"
    first, _ = parse_document(company.sources[0], body.encode(), "text/html", TODAY)
    other_source = company.sources[0].model_copy(update={"url": "https://example.com/culture"})
    second, _ = parse_document(
        other_source, ("<p>" + "인재상과 협업을 설명한다. " * 1000 + "</p>").encode(), "text/html", TODAY
    )
    payload = build_input(company, [first, second])
    document_rows = payload["documents"]
    assert isinstance(document_rows, list)
    assert len(document_rows) == 2
    texts = [passage["text"] for doc in document_rows for passage in doc["passages"]]
    assert sum(map(len, texts)) <= MAX_INPUT_CHARS
    assert any("주식회사 테스트" in text for text in texts)


def test_cache_fingerprint_uses_actual_passages(company: CompanySeed) -> None:
    source, _ = parse_document(
        company.sources[0], ("<p>" + "본문 자료를 읽는다. " * 20 + "</p>").encode(), "text/html", TODAY
    )
    changed = source.model_copy(deep=True)
    changed.passages[0].text += " 수정한 본문"
    assert source.content_hash == changed.content_hash
    assert fingerprint(company, [source], "test") != fingerprint(company, [changed], "test")


def test_dart_identity_enrichment_only_for_matching_entity() -> None:
    source = Path(__file__).resolve().parents[2] / "company_seed/data/drafts/naver.json"
    package = Package.model_validate_json(source.read_text())
    # Test identity values only; no fixture identity is written to real company data.
    identity = DartIdentity(
        corp_code="12345678", legal_name=package.legal_name, business_reg_no="1234567890", checked_at=TODAY
    )
    enriched = enrich(package, identity)
    assert enriched.dart_corp_code == "12345678"
    assert enriched.review.reviewed_by is None
    assert all("crtfc_key" not in source.url for source in enriched.sources)
    identity.legal_name = "다른 법인"
    with pytest.raises(ValueError, match="법인명이 달라"):
        enrich(package, identity)


async def test_privacy_document_is_identity_only(tmp_path: Path) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await DemoCollector(client, delay=0).collect(COMPANY, tmp_path, TODAY)
    documents = cached_documents(COMPANY, tmp_path)
    documents[0].source.source_type = "ABOUT_PAGE"
    documents[0].source.url = "https://example.invalid/privacy_policy"
    result = await DemoExtractor().extract(COMPANY, documents)
    with pytest.raises(ValueError, match="법인 식별에만"):
        assemble(COMPANY, documents, result)


async def test_collection_deadline_keeps_progress(
    company: CompanySeed, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_timeout = asyncio.timeout
    monkeypatch.setattr("company_seed.pipeline.asyncio.timeout", lambda seconds: original_timeout(0.01))

    class SlowCollector(Collector):
        async def collect(self, company: CompanySeed, cache: Path, today: date) -> CollectionReport:
            report = await super().collect(company, cache, today)
            await asyncio.sleep(1)
            return report

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        output = tmp_path / "run"
        code = await execute("collect", [company], tmp_path / "cache", output, SlowCollector(client, delay=0), None)
    assert code == 1
    rows = json.loads((output / "report.json").read_text())
    assert rows[0]["collectionTimeout"] is True
    assert rows[0]["status"] == "partial"
    assert rows[0]["collection"]["documents"]
