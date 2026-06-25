
from __future__ import annotations

from typing import Protocol


class GithubTarballFetcherError(Exception):
    pass


class GithubTarballFetcher(Protocol):
    async def fetch(self, owner: str, repo: str, branch: str) -> bytes:
        ...
