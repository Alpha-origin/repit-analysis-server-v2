from __future__ import annotations

from typing import Any

from app.core.common.interview_qa.dto import Stage3Result
from app.core.common.interview_qa.exploration_session import ExplorationSession, ExplorationSpec
from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicTextClient
from app.core.common.interview_qa.prompts import (
    SYSTEM_PROMPT_STAGE4,
    build_initial_user_message,
)
from app.core.common.interview_qa.stage4_file_reader import Stage4FileReader
from app.core.common.interview_qa.tools import STAGE4_TOOLS

# /generate 의 탐색 루프 설정. 루프 자체는 ExplorationSession 이 /profile 과 공유한다.
# /generate 는 이전이 끝나면 삭제되므로, 동작이 바뀌지 않게 기존 값을 그대로 넘긴다.
_GENERATE_SPEC = ExplorationSpec(
    system_prompt=SYSTEM_PROMPT_STAGE4,
    tools=STAGE4_TOOLS,
    final_tool_name="generate_result",
    failure_message="면접 질문 생성에 실패했습니다. 잠시 후 다시 시도해 주세요.",
    log_name="stage4_llm_session",
)


class Stage4LlmSession:
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
            spec=_GENERATE_SPEC,
        )

    async def execute(self, portfolio_text: str, repos_tree: Stage3Result) -> dict[str, Any]:
        initial_message = build_initial_user_message(portfolio_text, repos_tree.tree_text)
        return await self._session.execute(initial_message, repos_tree.path_index)
