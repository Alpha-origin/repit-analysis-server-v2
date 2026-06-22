"""GithubTarballFetcher Port.

저장소 default branch 의 tarball(gzip 압축된 tar) 바이트를 한 번에 받는다.
HTTP 호출 및 redirect 처리(GitHub 의 codeload 도메인으로 302) 는 어댑터 책임.
"""

from __future__ import annotations

from typing import Protocol


class GithubTarballFetcherError(Exception):
    """tarball 다운로드 실패(네트워크/4xx/5xx/타임아웃)."""


class GithubTarballFetcher(Protocol):
    async def fetch(self, owner: str, repo: str, branch: str) -> bytes:
        """저장소의 tarball.gz 바이트를 돌려준다.

        Raises:
            GithubTarballFetcherError: 다운로드 실패.
        """
        ...
