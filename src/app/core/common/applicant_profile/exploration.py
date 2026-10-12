from __future__ import annotations

from typing import Any

from app.core.common.applicant_profile.prompt import SYSTEM_PROMPT_PROFILE, build_profile_initial_message
from app.core.common.applicant_profile.tools import PROFILE_TOOLS
from app.core.common.interview_qa.dto import Stage3Result
from app.core.common.interview_qa.exploration_session import ExplorationSession, ExplorationSpec
from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicTextClient
from app.core.common.interview_qa.stage4_file_reader import Stage4FileReader

_PROFILE_SPEC = ExplorationSpec(
    system_prompt=SYSTEM_PROMPT_PROFILE,
    tools=PROFILE_TOOLS,
    final_tool_name="submit_profile",
    failure_message="포트폴리오 분석에 실패했습니다. 잠시 후 다시 시도해 주세요.",
    log_name="applicant_profile.exploration",
)


class ProfileExploration:
    """Stage 4' — /generate 와 같은 탐색 루프에 profile 용 프롬프트와 종료 도구를 넣어 돌린다."""

    def __init__(
        self,
        client: AnthropicTextClient,
        file_reader: Stage4FileReader,
        text_model: str,
        max_turns: int,
        token_limit: int,
        response_max_tokens: int,
    ) -> None:
        self._session = ExplorationSession(
            client=client,
            file_reader=file_reader,
            text_model=text_model,
            max_turns=max_turns,
            token_limit=token_limit,
            response_max_tokens=response_max_tokens,
            spec=_PROFILE_SPEC,
        )

    async def execute(self, portfolio_text: str, repos_tree: Stage3Result, major: str | None) -> dict[str, Any]:
        initial_message = build_profile_initial_message(portfolio_text, repos_tree.tree_text, major)
        return await self._session.execute(initial_message, repos_tree.path_index)
