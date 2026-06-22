"""Stage 4 — LLM 코드 탐색 세션 + read_files / generate_result 루프.

흐름:
1. 초기 사용자 메시지(포트폴리오 + 파일 트리) 를 ``messages`` 에 푼다.
2. ``messages`` 전체를 매번 LLM 에 보낸다(Claude API 는 무상태).
3. assistant 응답에 ``read_files`` tool_use 가 있으면 파일 본문을 만들어 tool_result 로 push.
4. ``generate_result`` tool_use 면 input 을 반환 → 세션 종료.
5. 상한 도달 시:
   - 누적 input 토큰 > ``token_limit`` → "추가 파일 없이 즉시 결과 만들어라" 지시 메시지 push.
   - 왕복 수 ≥ ``max_turns`` → 루프 종료 → ``tool_choice`` 로 generate_result 강제 호출.
6. 강제 호출도 실패하면 ``PipelineError(500)`` 으로 상위에 전달.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.core.common.interview_qa.dto import Stage3Result
from app.core.common.interview_qa.errors import PipelineError
from app.core.common.interview_qa.ports.anthropic_text_client import (
    AnthropicTextClient,
    AnthropicTextClientError,
)
from app.core.common.interview_qa.prompts import (
    SYSTEM_PROMPT_STAGE4,
    build_initial_user_message,
)
from app.core.common.interview_qa.stage4_file_reader import Stage4FileReader
from app.core.common.interview_qa.tools import STAGE4_TOOLS

logger = logging.getLogger(__name__)


_GENERATE_FAILURE_MESSAGE = "면접 질문 생성에 실패했습니다. 잠시 후 다시 시도해 주세요."


class Stage4LlmSession:
    """LLM 탐색 세션 오케스트레이터.

    한 번 호출(execute)이 한 번의 /generate 요청 세션과 1:1 대응.
    내부에서 LLM 을 여러 번 호출하며 messages 를 누적한다.
    """

    def __init__(
        self,
        client: AnthropicTextClient,
        file_reader: Stage4FileReader,
        text_model: str,
        max_turns: int,
        token_limit: int,
        response_max_tokens: int,
    ) -> None:
        self._client = client
        self._reader = file_reader
        self._model = text_model
        self._max_turns = max_turns
        self._token_limit = token_limit
        self._response_max_tokens = response_max_tokens

    async def execute(self, portfolio_text: str, repos_tree: Stage3Result) -> dict[str, Any]:
        """탐색 세션을 돌려 ``generate_result`` 입력 dict 를 반환한다.

        Raises:
            PipelineError(500): 강제 종료에서도 generate_result 가 안 나온 경우.
        """
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": build_initial_user_message(portfolio_text, repos_tree.tree_text),
            }
        ]
        explored: set[str] = set()
        total_input_tokens = 0
        total_output_tokens = 0
        token_limit_warned = False

        for turn in range(self._max_turns):
            # 토큰 누적이 한도를 넘으면 "더 읽지 말고 결과 만들어라" 지시를 한 번 push.
            if total_input_tokens > self._token_limit and not token_limit_warned:
                messages.append(
                    {
                        "role": "user",
                        "content": "토큰 한도 도달. 추가 파일 없이 즉시 generate_result 를 호출하라.",
                    }
                )
                token_limit_warned = True

            response = await self._safe_call(messages, tool_choice=None)
            total_input_tokens += response.input_tokens
            total_output_tokens += response.output_tokens
            self._log_turn_usage(
                turn=turn + 1,
                response_input_tokens=response.input_tokens,
                response_output_tokens=response.output_tokens,
                total_input_tokens=total_input_tokens,
                total_output_tokens=total_output_tokens,
                forced=False,
            )
            assistant_blocks = response.content_blocks
            messages.append({"role": "assistant", "content": assistant_blocks})

            tool_use = _first_tool_use(assistant_blocks)
            if tool_use is None:
                # 도구 호출이 없으면 한 번 더 안내하고 다음 턴으로.
                messages.append(
                    {
                        "role": "user",
                        "content": "read_files 또는 generate_result 도구를 사용하라.",
                    }
                )
                continue

            tool_name = tool_use.get("name")
            tool_id = str(tool_use.get("id", ""))
            tool_input = tool_use.get("input")
            if not isinstance(tool_input, dict):
                tool_input = {}

            if tool_name == "generate_result":
                self._log_session_usage(
                    turn=turn + 1,
                    total_input_tokens=total_input_tokens,
                    total_output_tokens=total_output_tokens,
                    explored=len(explored),
                    forced=False,
                )
                logger.info(
                    "stage4_llm_session.done",
                    extra={
                        "turn": turn + 1,
                        "input_tokens": total_input_tokens,
                        "output_tokens": total_output_tokens,
                        "total_tokens": total_input_tokens + total_output_tokens,
                        "explored": len(explored),
                        "forced": False,
                    },
                )
                return tool_input

            if tool_name == "read_files":
                raw_paths = tool_input.get("paths")
                paths: list[str] = [str(p) for p in raw_paths] if isinstance(raw_paths, list) else []
                tool_result = self._reader.read_files(
                    paths,
                    repos_tree.path_index,
                    explored,
                )
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": json.dumps(tool_result, ensure_ascii=False),
                            }
                        ],
                    }
                )
                continue

            # 지원하지 않는 도구 이름.
            messages.append(
                {
                    "role": "user",
                    "content": "지원하지 않는 도구이다. read_files 또는 generate_result 를 호출하라.",
                }
            )

        # 루프 소진 — generate_result 강제 호출.
        logger.warning(
            "stage4_llm_session.max_turns_reached",
            extra={"max_turns": self._max_turns, "explored": len(explored)},
        )
        forced, forced_input_tokens, forced_output_tokens = await self._force_generate(messages)
        total_input_tokens += forced_input_tokens
        total_output_tokens += forced_output_tokens
        self._log_turn_usage(
            turn=self._max_turns + 1,
            response_input_tokens=forced_input_tokens,
            response_output_tokens=forced_output_tokens,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            forced=True,
        )
        self._log_session_usage(
            turn=self._max_turns,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            explored=len(explored),
            forced=True,
        )
        logger.info(
            "stage4_llm_session.done",
            extra={
                "turn": self._max_turns,
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "total_tokens": total_input_tokens + total_output_tokens,
                "explored": len(explored),
                "forced": True,
            },
        )
        return forced

    # ---------------- 내부 ----------------

    async def _force_generate(self, messages: list[dict[str, Any]]) -> tuple[dict[str, Any], int, int]:
        """``tool_choice`` 로 generate_result 호출만 허용해 강제 종료."""
        messages.append(
            {
                "role": "user",
                "content": ("추가 파일을 읽지 말고, 지금까지의 분석만으로 generate_result 를 호출하라."),
            }
        )
        response = await self._safe_call(
            messages,
            tool_choice={"type": "tool", "name": "generate_result"},
        )
        tool_use = _first_tool_use(response.content_blocks)
        if tool_use is None or tool_use.get("name") != "generate_result":
            raise PipelineError(500, _GENERATE_FAILURE_MESSAGE)
        tool_input = tool_use.get("input")
        if not isinstance(tool_input, dict):
            raise PipelineError(500, _GENERATE_FAILURE_MESSAGE)
        return tool_input, response.input_tokens, response.output_tokens

    @staticmethod
    def _log_turn_usage(
        *,
        turn: int,
        response_input_tokens: int,
        response_output_tokens: int,
        total_input_tokens: int,
        total_output_tokens: int,
        forced: bool,
    ) -> None:
        """Stage 4 Anthropic 호출 1회 단위 토큰 사용량을 남긴다."""
        logger.info(
            "stage4_llm_session.turn_usage turn=%s response_input_tokens=%s response_output_tokens=%s "
            "response_total_tokens=%s total_input_tokens=%s total_output_tokens=%s total_tokens=%s forced=%s",
            turn,
            response_input_tokens,
            response_output_tokens,
            response_input_tokens + response_output_tokens,
            total_input_tokens,
            total_output_tokens,
            total_input_tokens + total_output_tokens,
            forced,
            extra={
                "turn": turn,
                "response_input_tokens": response_input_tokens,
                "response_output_tokens": response_output_tokens,
                "response_total_tokens": response_input_tokens + response_output_tokens,
                "total_input_tokens": total_input_tokens,
                "total_output_tokens": total_output_tokens,
                "total_tokens": total_input_tokens + total_output_tokens,
                "forced": forced,
            },
        )

    @staticmethod
    def _log_session_usage(
        *,
        turn: int,
        total_input_tokens: int,
        total_output_tokens: int,
        explored: int,
        forced: bool,
    ) -> None:
        """Stage 4 세션 전체 토큰 사용량을 비교용 단일 로그로 남긴다."""
        logger.info(
            "stage4_llm_session.token_usage input_tokens=%s output_tokens=%s total_tokens=%s explored=%s forced=%s",
            total_input_tokens,
            total_output_tokens,
            total_input_tokens + total_output_tokens,
            explored,
            forced,
            extra={
                "turn": turn,
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "total_tokens": total_input_tokens + total_output_tokens,
                "explored": explored,
                "forced": forced,
            },
        )

    async def _safe_call(
        self,
        messages: list[dict[str, Any]],
        tool_choice: dict[str, Any] | None,
    ) -> Any:
        """LLM 호출의 네트워크/API 오류를 ``PipelineError(500)`` 으로 통일."""
        try:
            return await self._client.call(
                model=self._model,
                system=SYSTEM_PROMPT_STAGE4,
                messages=messages,
                tools=STAGE4_TOOLS,
                tool_choice=tool_choice,
                max_tokens=self._response_max_tokens,
            )
        except AnthropicTextClientError as exc:
            logger.warning("stage4_llm_session.api_error", extra={"error": str(exc)})
            raise PipelineError(500, _GENERATE_FAILURE_MESSAGE) from exc


# ---------------- 모듈 헬퍼 ----------------


def _first_tool_use(content_blocks: list[dict[str, Any]]) -> dict[str, Any] | None:
    """assistant 응답에서 첫 ``tool_use`` 블록을 찾는다."""
    for block in content_blocks:
        if block.get("type") == "tool_use":
            return block
    return None
