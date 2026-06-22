from collections.abc import Iterable

from dishka import Provider, Scope, provide

from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicTextClient
from app.core.common.interview_qa.ports.github_metadata_client import GithubMetadataClient
from app.core.common.interview_qa.ports.github_tarball_fetcher import GithubTarballFetcher
from app.core.common.interview_qa.ports.pdf_fetcher import PdfFetcher
from app.core.common.interview_qa.ports.webhook_client import WebhookClient
from app.main.config import AnthropicSettings, InterviewQaSettings
from app.outbound.adapters.anthropic_text_client_impl import AnthropicTextClientImpl
from app.outbound.adapters.httpx_github_metadata_client import HttpxGithubMetadataClient
from app.outbound.adapters.httpx_github_tarball_fetcher import HttpxGithubTarballFetcher
from app.outbound.adapters.httpx_pdf_fetcher import HttpxPdfFetcher
from app.outbound.adapters.httpx_webhook_client import HttpxWebhookClient


class OutboundProvider(Provider):
    """외부 시스템 어댑터를 등록하는 Provider.

    각 Port(Protocol) 의 실제 구현체를 묶어 제공한다.
    테스트에서는 다른 Provider 로 교체해 가짜 구현을 주입할 수 있다.
    """

    scope = Scope.REQUEST

    @provide
    def webhook_client(self, settings: InterviewQaSettings) -> WebhookClient:
        """콜백 POST 어댑터."""
        return HttpxWebhookClient(
            timeout_seconds=settings.WEBHOOK_TIMEOUT_SECONDS,
            retry_delay_seconds=settings.WEBHOOK_RETRY_DELAY_SECONDS,
        )

    @provide
    def pdf_fetcher(self, settings: InterviewQaSettings) -> PdfFetcher:
        """포트폴리오 PDF 다운로드 어댑터."""
        return HttpxPdfFetcher(timeout_seconds=settings.PDF_DOWNLOAD_TIMEOUT_SECONDS)

    @provide
    def github_metadata_client(self, settings: InterviewQaSettings) -> GithubMetadataClient:
        """GitHub 메타데이터 조회 어댑터."""
        return HttpxGithubMetadataClient(timeout_seconds=settings.GITHUB_API_TIMEOUT_SECONDS)

    @provide
    def github_tarball_fetcher(self, settings: InterviewQaSettings) -> GithubTarballFetcher:
        """GitHub tarball 다운로드 어댑터."""
        return HttpxGithubTarballFetcher(timeout_seconds=settings.TARBALL_TIMEOUT_SECONDS)

    @provide
    def anthropic_text_client(self, settings: AnthropicSettings) -> AnthropicTextClient:
        """Anthropic 텍스트(+ tool-use) 호출 어댑터."""
        return AnthropicTextClientImpl(api_key=settings.API_KEY)


def outbound_providers() -> Iterable[Provider]:
    return (OutboundProvider(),)
