"""Build the process-wide callback security provider (kept free of model/SDK imports)."""

from __future__ import annotations

import logging

from app.main.config import CallbackSecuritySettings, InterviewQaSettings
from app.outbound.adapters.runtime_security_provider import StaticRuntimeSecurityProvider

logger = logging.getLogger(__name__)


def check_pdf_source_hosts(interview_qa: InterviewQaSettings, security: CallbackSecuritySettings) -> None:
    """포트폴리오 PDF 허용 호스트가 비면 모든 /profile 이 422 가 된다.

    운영에서 환경 변수를 빠뜨린 채 떠서 사용자 오류로 처음 알게 되지 않도록 production 에서는 부팅을 막고,
    그 밖의 환경에서는 경고만 남긴다.
    """
    if interview_qa.PDF_SOURCE_ALLOWED_HOSTS:
        return
    if security.ENVIRONMENT == "production":
        raise RuntimeError("INTERVIEW_QA_PDF_SOURCE_ALLOWED_HOSTS is required in production")
    logger.warning(
        "INTERVIEW_QA_PDF_SOURCE_ALLOWED_HOSTS 가 비어 있어 모든 포트폴리오 PDF 를 거절합니다. "
        "API 서버 S3_URL 의 호스트를 넣어 주세요."
    )


def runtime_security_from(
    settings: CallbackSecuritySettings, blocked_source_hosts: tuple[str, ...] = ()
) -> StaticRuntimeSecurityProvider:
    token = settings.INTERNAL_CALLBACK_TOKEN
    return StaticRuntimeSecurityProvider(
        environment=settings.ENVIRONMENT,
        callback_token=token.get_secret_value() if token is not None else None,
        allowed_callback_hosts=settings.CALLBACK_ALLOWED_HOSTS,
        blocked_callback_hosts=settings.CALLBACK_BLOCKED_HOSTS,
        blocked_source_hosts=blocked_source_hosts,
    )
