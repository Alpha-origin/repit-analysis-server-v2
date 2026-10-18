from __future__ import annotations

import logging

import httpx

from app.core.common.interview_qa.ports.pdf_fetcher import PdfFetcherError
from app.core.common.security.url_policy import UrlPolicyError, check_source_url

logger = logging.getLogger(__name__)


class HttpxPdfFetcher:
    def __init__(
        self,
        timeout_seconds: int,
        allowed_hosts: frozenset[str],
        max_bytes: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # timeout·allowed_hosts·max_bytes 는 OutboundProvider 가 Settings 에서 꺼내 전달한다.
        # ``transport`` 는 테스트에서 MockTransport 를 주입하기 위한 hook.
        self._timeout = timeout_seconds
        self._allowed_hosts = allowed_hosts
        self._max_bytes = max_bytes
        self._transport = transport

    async def fetch(self, url: str) -> bytes:
        # 주소는 사용자 자료에서 온다. 운영자가 허용한 S3 호스트만 받아 내부망·임의 호스트로의 요청(SSRF)을 막는다.
        try:
            check_source_url(url, self._allowed_hosts, frozenset())
        except UrlPolicyError as exc:
            logger.warning("pdf_fetcher.url_rejected", extra={"reason": exc.reason})
            raise PdfFetcherError("허용되지 않은 PDF 주소") from exc

        try:
            async with (
                httpx.AsyncClient(
                    timeout=self._timeout,
                    transport=self._transport,
                    # 허용 목록을 우회하지 못하게 리다이렉트는 따라가지 않는다. S3 객체 주소는 리다이렉트하지 않는다.
                    follow_redirects=False,
                ) as client,
                client.stream("GET", url) as response,
            ):
                if not response.is_success:
                    # 3xx/4xx/5xx 도 동일하게 실패로 처리.
                    logger.warning(
                        "pdf_fetcher.non_2xx",
                        extra={"url": url, "status_code": response.status_code},
                    )
                    raise PdfFetcherError(f"PDF 다운로드 실패 (status={response.status_code})")
                return await self._read_limited(response, url)
        except httpx.HTTPError as exc:
            # 네트워크/타임아웃 등 — 도메인은 "다운로드 실패" 하나로만 알면 충분.
            logger.warning("pdf_fetcher.network_error", extra={"url": url, "error": str(exc)})
            raise PdfFetcherError("PDF 다운로드 실패") from exc

    async def _read_limited(self, response: httpx.Response, url: str) -> bytes:
        """옛 업로드 경로는 같은 버킷에 1GB 까지 올렸다. 받는 동안 상한을 넘으면 끊어 메모리를 지킨다."""
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > self._max_bytes:
            logger.warning("pdf_fetcher.too_large", extra={"url": url, "content_length": int(declared)})
            raise PdfFetcherError("PDF 가 너무 큽니다")
        chunks: list[bytes] = []
        received = 0
        async for chunk in response.aiter_bytes():
            received += len(chunk)
            if received > self._max_bytes:
                logger.warning("pdf_fetcher.too_large", extra={"url": url, "received": received})
                raise PdfFetcherError("PDF 가 너무 큽니다")
            chunks.append(chunk)
        return b"".join(chunks)
