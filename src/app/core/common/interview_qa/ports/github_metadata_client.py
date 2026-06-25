
from __future__ import annotations

from typing import Protocol

from app.core.common.interview_qa.dto import RepoMeta


class GithubMetadataClientError(Exception):
    pass

class GithubMetadataClient(Protocol):


    async def get_repo(self, owner: str, repo: str) -> RepoMeta | None:
        ...
