"""httpx 기반 GithubTarballFetcher 어댑터.

``GET https://api.github.com/repos/{owner}/{repo}/tarball/{branch}`` 호출.
이 엔드포인트는 codeload.github.com 으로 302 redirect 하므로 ``follow_redirects=True``.
응답 본문(tarball.gz) 을 메모리로 읽어 그대로 돌려준다.
"""

from __future__ import annotations

import logging

import httpx

from app.core.common.interview_qa.ports.github_tarball_fetcher import GithubTarballFetcherError

logger = logging.getLogger(__name__)

_GITHUB_API_BASE = "https://api.github.com"


class HttpxGithubTarballFetcher:
    def __init__(
        self,
        timeout_seconds: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._transport = transport

    async def fetch(self, owner: str, repo: str, branch: str) -> bytes:
        url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/tarball/{branch}"
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
                follow_redirects=True,
            ) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            logger.warning("github_tarball.network_error", extra={"url": url, "error": str(exc)})
            raise GithubTarballFetcherError("GitHub tarball 다운로드 실패") from exc

        if not response.is_success:
            logger.warning(
                "github_tarball.non_2xx",
                extra={"url": url, "status_code": response.status_code},
            )
            raise GithubTarballFetcherError(f"GitHub tarball 다운로드 실패 (status={response.status_code})")

        return response.content
