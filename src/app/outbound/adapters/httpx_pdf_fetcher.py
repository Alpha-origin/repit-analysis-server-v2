"""httpx 기반 PdfFetcher 어댑터.

주어진 URL 로 GET 요청을 보내 PDF 파일 바이트를 받아온다.
3xx redirect 는 따라간다. 4xx/5xx 응답·네트워크 오류·타임아웃은 모두
``PdfFetcherError`` 로 변환해 도메인 서비스에 전달한다.
"""

from __future__ import annotations

import logging

import httpx

from app.core.common.interview_qa.ports.pdf_fetcher import PdfFetcherError

logger = logging.getLogger(__name__)


class HttpxPdfFetcher:
    """``PdfFetcher`` Protocol 의 httpx 구현체."""

    def __init__(
        self,
        timeout_seconds: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # timeout 은 OutboundProvider 가 Settings 에서 꺼내 전달한다.
        # ``transport`` 는 테스트에서 MockTransport 를 주입하기 위한 hook.
        self._timeout = timeout_seconds
        self._transport = transport

    async def fetch(self, url: str) -> bytes:
        """PDF 파일을 다운로드한다. 실패는 ``PdfFetcherError`` 로 통일."""
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
                # 포트폴리오는 호스팅 서비스 redirect 가 흔하므로 따라간다.
                follow_redirects=True,
            ) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            # 네트워크/타임아웃 등 — 도메인은 "다운로드 실패" 하나로만 알면 충분.
            logger.warning("pdf_fetcher.network_error", extra={"url": url, "error": str(exc)})
            raise PdfFetcherError("PDF 다운로드 실패") from exc

        if not response.is_success:
            # 4xx/5xx 도 동일하게 실패로 처리.
            logger.warning(
                "pdf_fetcher.non_2xx",
                extra={"url": url, "status_code": response.status_code},
            )
            raise PdfFetcherError(f"PDF 다운로드 실패 (status={response.status_code})")

        return response.content
