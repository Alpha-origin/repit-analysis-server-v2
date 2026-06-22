"""GithubMetadataClient Port.

GitHub 저장소 메타데이터(존재 여부 / public 여부 / default branch) 조회 책임을 정의한다.
GitHub API 호출 구현은 outbound 어댑터에서.
"""

from __future__ import annotations

from typing import Protocol

from app.core.common.interview_qa.dto import RepoMeta


class GithubMetadataClientError(Exception):
    """GitHub API 호출 자체가 실패했음을 나타낸다.

    네트워크 오류, 인증 외 5xx 응답 등. Stage 1 검증 서비스가
    ``PipelineError(403)`` 으로 변환한다(외부에 노출할 때는 동일하게 처리).
    """


class GithubMetadataClient(Protocol):
    """GitHub 저장소 메타데이터 조회 인터페이스."""

    async def get_repo(self, owner: str, repo: str) -> RepoMeta | None:
        """저장소 메타데이터를 조회한다.

        Returns:
            ``RepoMeta`` — 저장소가 존재. ``is_private`` 로 public/private 판별.
            ``None`` — 저장소가 존재하지 않음(404).

        Raises:
            GithubMetadataClientError: 호출 자체가 실패(네트워크/5xx).
        """
        ...
