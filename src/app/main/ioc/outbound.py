from collections.abc import Iterable

from dishka import Provider, Scope, provide

from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicTextClient
from app.core.common.interview_qa.ports.github_metadata_client import GithubMetadataClient
from app.core.common.interview_qa.ports.github_tarball_fetcher import GithubTarballFetcher
from app.core.common.interview_qa.ports.pdf_fetcher import PdfFetcher
from app.core.common.interview_qa.ports.webhook_client import WebhookClient
from app.core.common.security.ports import RuntimeSecurityProvider
from app.main.config import AnthropicSettings, CallbackSecuritySettings, InterviewQaSettings
from app.main.security_bootstrap import runtime_security_from
from app.outbound.adapters.anthropic_text_client_impl import AnthropicTextClientImpl
from app.outbound.adapters.httpx_github_metadata_client import HttpxGithubMetadataClient
from app.outbound.adapters.httpx_github_tarball_fetcher import HttpxGithubTarballFetcher
from app.outbound.adapters.httpx_pdf_fetcher import HttpxPdfFetcher
from app.outbound.adapters.httpx_webhook_client import HttpxWebhookClient


class OutboundProvider(Provider):
    scope = Scope.REQUEST

    @provide(scope=Scope.APP)
    def runtime_security(self, settings: CallbackSecuritySettings) -> RuntimeSecurityProvider:
        return runtime_security_from(settings)

    @provide
    def webhook_client(self, settings: InterviewQaSettings, security: RuntimeSecurityProvider) -> WebhookClient:
        return HttpxWebhookClient(
            timeout_seconds=settings.WEBHOOK_TIMEOUT_SECONDS,
            retry_delay_seconds=settings.WEBHOOK_RETRY_DELAY_SECONDS,
            security=security,
        )

    @provide
    def pdf_fetcher(self, settings: InterviewQaSettings) -> PdfFetcher:
        return HttpxPdfFetcher(timeout_seconds=settings.PDF_DOWNLOAD_TIMEOUT_SECONDS)

    @provide
    def github_metadata_client(self, settings: InterviewQaSettings) -> GithubMetadataClient:
        return HttpxGithubMetadataClient(timeout_seconds=settings.GITHUB_API_TIMEOUT_SECONDS)

    @provide
    def github_tarball_fetcher(self, settings: InterviewQaSettings) -> GithubTarballFetcher:
        return HttpxGithubTarballFetcher(timeout_seconds=settings.TARBALL_TIMEOUT_SECONDS)

    @provide
    def anthropic_text_client(self, settings: AnthropicSettings) -> AnthropicTextClient:
        return AnthropicTextClientImpl(api_key=settings.API_KEY)


def outbound_providers() -> Iterable[Provider]:
    return (OutboundProvider(),)
