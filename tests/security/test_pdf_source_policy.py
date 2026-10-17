"""포트폴리오 PDF 는 운영자가 허용한 S3 호스트에서만 받는다(SSRF 방지).

PDF 주소는 사용자 자료에서 오고, 인증 서버를 직접 불러 아무 주소나 저장할 수 있다. 그 주소를 그대로 내려받으면
내부망·메타데이터 주소로 요청이 나간다.
"""

from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from app.core.common.interview_qa.ports.pdf_fetcher import PdfFetcherError
from app.main.config import InterviewQaSettings
from app.outbound.adapters.httpx_pdf_fetcher import HttpxPdfFetcher

BUCKET_HOST = "repit-storage-dev.s3.ap-northeast-2.amazonaws.com"
OBJECT_URL = f"https://{BUCKET_HOST}/0b7c8f0e-1d2a-4c3b-9a8e-5f6d7c8b9a01.pdf"
PDF_BYTES = b"%PDF-1.7 fake"


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.response


def fetcher(transport: RecordingTransport, hosts: frozenset[str] = frozenset({BUCKET_HOST})) -> HttpxPdfFetcher:
    return HttpxPdfFetcher(timeout_seconds=5, allowed_hosts=hosts, transport=transport)


@pytest.mark.asyncio
async def test_allowed_bucket_object_is_downloaded() -> None:
    transport = RecordingTransport(httpx.Response(200, content=PDF_BYTES))

    assert await fetcher(transport).fetch(OBJECT_URL) == PDF_BYTES
    assert [str(request.url) for request in transport.requests] == [OBJECT_URL]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "https://169.254.169.254/latest/meta-data/",
        "https://localhost/portfolio.pdf",
        "https://127.0.0.1/portfolio.pdf",
        "https://api.repit.internal/portfolio.pdf",
        f"http://{BUCKET_HOST}/a.pdf",
        f"https://{BUCKET_HOST}:8443/a.pdf",
        f"https://user@{BUCKET_HOST}/a.pdf",
        "https://other-bucket.s3.ap-northeast-2.amazonaws.com/a.pdf",
        f"https://{BUCKET_HOST}/",
    ],
)
async def test_other_destinations_are_rejected_without_network(url: str) -> None:
    transport = RecordingTransport(httpx.Response(200, content=PDF_BYTES))

    with pytest.raises(PdfFetcherError):
        await fetcher(transport).fetch(url)
    assert transport.requests == []


@pytest.mark.asyncio
async def test_nothing_is_downloaded_when_no_host_is_configured() -> None:
    transport = RecordingTransport(httpx.Response(200, content=PDF_BYTES))

    with pytest.raises(PdfFetcherError):
        await fetcher(transport, hosts=frozenset()).fetch(OBJECT_URL)
    assert transport.requests == []


@pytest.mark.asyncio
async def test_redirect_is_not_followed() -> None:
    transport = RecordingTransport(
        httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data/"})
    )

    with pytest.raises(PdfFetcherError):
        await fetcher(transport).fetch(OBJECT_URL)
    assert len(transport.requests) == 1


def test_allowed_hosts_setting_is_split_lowercased_and_empty_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INTERVIEW_QA_PDF_SOURCE_ALLOWED_HOSTS", raising=False)
    assert InterviewQaSettings(_env_file=None).PDF_SOURCE_ALLOWED_HOSTS == ()

    monkeypatch.setenv("INTERVIEW_QA_PDF_SOURCE_ALLOWED_HOSTS", f" {BUCKET_HOST.upper()}, other.s3.amazonaws.com ")
    assert InterviewQaSettings(_env_file=None).PDF_SOURCE_ALLOWED_HOSTS == (
        BUCKET_HOST,
        "other.s3.amazonaws.com",
    )


def test_ip_literal_cannot_be_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERVIEW_QA_PDF_SOURCE_ALLOWED_HOSTS", "169.254.169.254")

    with pytest.raises(ValidationError):
        InterviewQaSettings(_env_file=None)
