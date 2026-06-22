"""httpx 기반 GithubMetadataClient 어댑터.

``GET https://api.github.com/repos/{owner}/{repo}`` 를 비인증으로 호출하고
응답을 ``RepoMeta`` 로 변환한다. 토큰이 없으므로 rate limit 이 빠듯하지만
v1 에서는 그대로 둔다(에러가 빈번하면 토큰 도입을 검토).

응답 매핑:
  - 200: 본문에서 ``owner.login``, ``name``, ``default_branch``, ``private`` 추출.
  - 404: ``None`` 반환(저장소 없음).
  - 그 외(403/5xx 등): ``GithubMetadataClientError``.
"""

from __future__ import annotations

import logging

import httpx

from app.core.common.interview_qa.dto import RepoMeta
from app.core.common.interview_qa.ports.github_metadata_client import GithubMetadataClientError

logger = logging.getLogger(__name__)

# GitHub REST API 의 베이스 URL. 외부 의존을 코드 한 곳에 묶어두기 위한 상수.
_GITHUB_API_BASE = "https://api.github.com"


class HttpxGithubMetadataClient:
    """``GithubMetadataClient`` Protocol 의 httpx 구현체."""

    def __init__(
        self,
        timeout_seconds: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._transport = transport

    async def get_repo(self, owner: str, repo: str) -> RepoMeta | None:
        """저장소 메타데이터 조회.

        404 는 ``None`` 으로 매핑(저장소가 존재하지 않는 정상 상태).
        그 외 오류는 ``GithubMetadataClientError`` 로 변환.
        """
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
