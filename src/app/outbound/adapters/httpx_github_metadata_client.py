from __future__ import annotations

import logging

import httpx

from app.core.common.interview_qa.dto import RepoMeta
from app.core.common.interview_qa.ports.github_metadata_client import GithubMetadataClientError

logger = logging.getLogger(__name__)

# GitHub REST API 의 베이스 URL. 외부 의존을 코드 한 곳에 묶어두기 위한 상수.
_GITHUB_API_BASE = "https://api.github.com"


class HttpxGithubMetadataClient:
    def __init__(
        self,
        timeout_seconds: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._transport = transport

    async def get_repo(self, owner: str, repo: str) -> RepoMeta | None:
        url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}"
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            logger.warning("github.network_error", extra={"url": url, "error": str(exc)})
            raise GithubMetadataClientError("GitHub API 호출 실패") from exc

        if response.status_code == httpx.codes.NOT_FOUND:
            # 404 는 "저장소 없음" — 정상 흐름이므로 None 반환.
            return None

        if not response.is_success:
            # 그 외 비2xx 는 호출 자체 실패로 본다(rate limit, 5xx 등).
            logger.warning(
                "github.non_2xx",
                extra={"url": url, "status_code": response.status_code},
            )
            raise GithubMetadataClientError(f"GitHub API 호출 실패 (status={response.status_code})")

        # 응답 JSON 에서 필요한 필드만 추출. 키가 빠져 있으면 호출 실패로 본다.
        try:
            data = response.json()
            return RepoMeta(
                owner=data["owner"]["login"],
                name=data["name"],
                default_branch=data["default_branch"],
                is_private=bool(data["private"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("github.invalid_payload", extra={"url": url, "error": str(exc)})
            raise GithubMetadataClientError("GitHub API 응답 형식이 예상과 다름") from exc
