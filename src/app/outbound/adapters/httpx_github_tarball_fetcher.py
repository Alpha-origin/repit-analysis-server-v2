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
