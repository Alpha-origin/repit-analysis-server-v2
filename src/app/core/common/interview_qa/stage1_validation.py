
from __future__ import annotations

import logging
from urllib.parse import urlparse

import fitz  # PyMuPDF — PDF 파싱 라이브러리

from app.core.common.interview_qa.dto import RepoMeta, Stage1Result
from app.core.common.interview_qa.errors import PipelineError
from app.core.common.interview_qa.ports.github_metadata_client import (
    GithubMetadataClient,
    GithubMetadataClientError,
)
from app.core.common.interview_qa.ports.pdf_fetcher import PdfFetcher, PdfFetcherError

logger = logging.getLogger(__name__)


# 외부에 노출되는 한글 메시지를 한 곳에 모아둔다. 단어 하나만 다른 표현이
# 코드 곳곳에 흩어지지 않도록.
_PDF_INVALID_MESSAGE = "유효한 PDF 파일이 아닙니다. 진행할 수 없습니다."
_REPO_NOT_PUBLIC_MESSAGE = "github 저장소 상태를 public으로 변경해주세요."
_REPO_URL_FORMAT_MESSAGE = "GitHub 저장소 URL 형식이 올바르지 않습니다."

# GitHub URL path 에서 최소한 owner / repo 두 컴포넌트가 필요하다.
_MIN_GITHUB_PATH_PARTS = 2


class Stage1Validation:


    def __init__(
        self,
        pdf_fetcher: PdfFetcher,
        github_metadata_client: GithubMetadataClient,
    ) -> None:
        self._pdf = pdf_fetcher
        self._github = github_metadata_client

    async def execute(self, portfolio_url: str, github_urls: tuple[str, ...]) -> Stage1Result:
        pdf_bytes = await self._validate_pdf(portfolio_url)
        repos = await self._validate_repos(github_urls)

        # 종료 로그 — INFO 에는 메타만, DEBUG 에는 자세한 값.
        logger.info(
            "stage1.done",
            extra={
                "pdf_bytes_len": len(pdf_bytes),
                "repo_count": len(repos),
                "repos": [{"owner": r.owner, "repo": r.name, "branch": r.default_branch} for r in repos],
            },
        )
        logger.debug(
            "stage1.payload",
            extra={
                "portfolio_url": portfolio_url,
                "github_urls": list(github_urls),
            },
        )
        return Stage1Result(pdf_bytes=pdf_bytes, repos=tuple(repos))

    # ---------------- PDF 검증 ----------------

    async def _validate_pdf(self, url: str) -> bytes:
        try:
            data = await self._pdf.fetch(url)
        except PdfFetcherError:
            # 네트워크/4xx/5xx 류 — 422 로 변환.
            raise PipelineError(422, _PDF_INVALID_MESSAGE) from None

        if not data:
            # 0바이트 응답도 PDF 로 볼 수 없음.
            raise PipelineError(422, _PDF_INVALID_MESSAGE)

        # fitz 가 부적합한 입력에 대해 다양한 예외를 던질 수 있어 한꺼번에 잡는다.
        # 정상이면 page_count 가 1 이상이어야 한다.
        try:
            with fitz.open(stream=data, filetype="pdf") as doc:
                if doc.page_count < 1:
                    raise PipelineError(422, _PDF_INVALID_MESSAGE)
        except PipelineError:
            raise
        except Exception:
            raise PipelineError(422, _PDF_INVALID_MESSAGE) from None

        return data

    # ---------------- GitHub 검증 ----------------

    async def _validate_repos(self, urls: tuple[str, ...]) -> list[RepoMeta]:
        results: list[RepoMeta] = []
        for url in urls:
            owner, repo = self._parse_github_url(url)
            try:
                meta = await self._github.get_repo(owner, repo)
            except GithubMetadataClientError:
                # API 호출 자체가 실패 — 사용자가 액션할 수 있는 메시지로 통일.
                raise PipelineError(403, _REPO_NOT_PUBLIC_MESSAGE) from None

            if meta is None or meta.is_private:
                # 404 또는 private — 동일하게 "public 으로 변경" 안내.
                raise PipelineError(403, _REPO_NOT_PUBLIC_MESSAGE)
            results.append(meta)
        return results

    def _parse_github_url(self, url: str) -> tuple[str, str]:
        # urlparse 결과의 scheme 은 http/https, host 는 github.com 류여야 한다.
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise PipelineError(422, _REPO_URL_FORMAT_MESSAGE)
        if parsed.hostname not in ("github.com", "www.github.com"):
            raise PipelineError(422, _REPO_URL_FORMAT_MESSAGE)

        # 경로를 슬래시로 잘라 owner / repo 만 본다.
        # `/octocat/Hello-World` → ['octocat', 'Hello-World']
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) < _MIN_GITHUB_PATH_PARTS:
            raise PipelineError(422, _REPO_URL_FORMAT_MESSAGE)

        owner = parts[0]
        # `Hello-World.git` 처럼 git suffix 가 붙으면 제거.
        repo = parts[1].removesuffix(".git")
        if not owner or not repo:
            raise PipelineError(422, _REPO_URL_FORMAT_MESSAGE)
        return owner, repo
