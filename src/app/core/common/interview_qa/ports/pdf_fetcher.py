"""PdfFetcher Port.

포트폴리오 PDF URL 에서 파일 바이트를 가져오는 책임을 정의한다.
실제 HTTP 호출 구현은 outbound 어댑터에서.
"""

from __future__ import annotations

from typing import Protocol


class PdfFetcherError(Exception):
    """PDF 다운로드 자체가 실패했음을 나타낸다.

    Stage 1 검증 서비스가 이 예외를 잡아 ``PipelineError(422)`` 로 변환한다.
    네트워크 오류, 4xx/5xx 응답, 타임아웃 등이 모두 여기에 해당.
    """


class PdfFetcher(Protocol):
    """PDF 파일 다운로드 인터페이스."""

    async def fetch(self, url: str) -> bytes:
        """주어진 URL 에서 PDF 파일 바이트를 가져온다.

        Returns:
            응답 본문 바이트.

        Raises:
            PdfFetcherError: 다운로드 실패(네트워크 오류 / 비2xx 응답 / 타임아웃).
        """
        ...
