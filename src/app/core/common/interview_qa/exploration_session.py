from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from app.core.common.interview_qa.errors import PipelineError
from app.core.common.interview_qa.ports.anthropic_text_client import (
    AnthropicTextClient,
    AnthropicTextClientError,
)
from app.core.common.interview_qa.stage4_file_reader import Stage4FileReader

logger = logging.getLogger(__name__)

_READ_FILES_TOOL_NAME = "read_files"


@dataclass(frozen=True)
class ExplorationSpec:
    """탐색 루프 하나가 쓰는 프롬프트·도구 묶음.

    /generate 와 /profile 은 루프(read_files 왕복 → 종료 도구 호출)가 같고
    무엇을 지시하고 무엇으로 끝내는지만 다르다. 그 차이를 여기에 모은다.
    """

    system_prompt: str
    # read_files 와 종료 도구를 함께 담는다. 종료 도구 이름은 final_tool_name 과 같아야 한다.
    tools: list[dict[str, Any]]
    final_tool_name: str
    # LLM 호출이 실패하거나 종료 도구를 끝내 부르지 않았을 때 콜백에 실을 문구.
    failure_message: str
    # 로그 이벤트 접두어. /generate 는 기존 대시보드가 보는 "stage4_llm_session" 을 유지한다.
    log_name: str


class ExplorationSession:
    def __init__(
        self,
        client: AnthropicTextClient,
        file_reader: Stage4FileReader,
        text_model: str,
        max_turns: int,
        token_limit: int,
        response_max_tokens: int,
        spec: ExplorationSpec,
    ) -> None:
        self._client = client
        self._reader = file_reader
        self._model = text_model
        self._max_turns = max_turns
        self._token_limit = token_limit
        self._response_max_tokens = response_max_tokens
        self._spec = spec

    async def execute(self, initial_message: str, path_index: dict[str, str]) -> dict[str, Any]:
        """종료 도구의 입력(dict) 을 돌려준다. 형식 검증은 호출측 몫이다."""
        final_tool = self._spec.final_tool_name
        messages: list[dict[str, Any]] = [{"role": "user", "content": initial_message}]
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
                        "content": f"토큰 한도 도달. 추가 파일 없이 즉시 {final_tool} 를 호출하라.",
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
                        "content": f"{_READ_FILES_TOOL_NAME} 또는 {final_tool} 도구를 사용하라.",
                    }
                )
                continue

            tool_name = tool_use.get("name")
            tool_id = str(tool_use.get("id", ""))
            tool_input = tool_use.get("input")
            if not isinstance(tool_input, dict):
                tool_input = {}

            if tool_name == final_tool:
                self._log_done(
                    turn=turn + 1,
                    total_input_tokens=total_input_tokens,
                    total_output_tokens=total_output_tokens,
                    explored=len(explored),
                    forced=False,
                )
                return tool_input

            if tool_name == _READ_FILES_TOOL_NAME:
                raw_paths = tool_input.get("paths")
                paths: list[str] = [str(p) for p in raw_paths] if isinstance(raw_paths, list) else []
                tool_result = self._reader.read_files(paths, path_index, explored)
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
                    "content": f"지원하지 않는 도구이다. {_READ_FILES_TOOL_NAME} 또는 {final_tool} 를 호출하라.",
                }
            )

        # 루프 소진 — 종료 도구 강제 호출.
        logger.warning(
            f"{self._spec.log_name}.max_turns_reached",
            extra={"max_turns": self._max_turns, "explored": len(explored)},
        )
        forced, forced_input_tokens, forced_output_tokens = await self._force_final(messages)
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
        self._log_done(
            turn=self._max_turns,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            explored=len(explored),
            forced=True,
        )
        return forced

    # ---------------- 내부 ----------------

    async def _force_final(self, messages: list[dict[str, Any]]) -> tuple[dict[str, Any], int, int]:
        final_tool = self._spec.final_tool_name
        messages.append(
            {
                "role": "user",
                "content": f"추가 파일을 읽지 말고, 지금까지의 분석만으로 {final_tool} 를 호출하라.",
            }
        )
        response = await self._safe_call(
            messages,
            tool_choice={"type": "tool", "name": final_tool},
        )
        tool_use = _first_tool_use(response.content_blocks)
        if tool_use is None or tool_use.get("name") != final_tool:
            raise PipelineError(500, self._spec.failure_message)
        tool_input = tool_use.get("input")
        if not isinstance(tool_input, dict):
            raise PipelineError(500, self._spec.failure_message)
        return tool_input, response.input_tokens, response.output_tokens

    def _log_turn_usage(
        self,
        *,
        turn: int,
        response_input_tokens: int,
        response_output_tokens: int,
        total_input_tokens: int,
        total_output_tokens: int,
        forced: bool,
    ) -> None:
        logger.info(
            f"{self._spec.log_name}.turn_usage turn=%s response_input_tokens=%s response_output_tokens=%s "
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

    def _log_done(
        self,
        *,
        turn: int,
        total_input_tokens: int,
        total_output_tokens: int,
        explored: int,
        forced: bool,
    ) -> None:
        logger.info(
            f"{self._spec.log_name}.token_usage input_tokens=%s output_tokens=%s total_tokens=%s explored=%s forced=%s",
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
        logger.info(
            f"{self._spec.log_name}.done",
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
        try:
            return await self._client.call(
                model=self._model,
                system=self._spec.system_prompt,
                messages=messages,
                tools=self._spec.tools,
                tool_choice=tool_choice,
                max_tokens=self._response_max_tokens,
            )
        except AnthropicTextClientError as exc:
            logger.warning(f"{self._spec.log_name}.api_error", extra={"error": str(exc)})
            raise PipelineError(500, self._spec.failure_message) from exc


# ---------------- 모듈 헬퍼 ----------------


def _first_tool_use(content_blocks: list[dict[str, Any]]) -> dict[str, Any] | None:
    for block in content_blocks:
        if block.get("type") == "tool_use":
            return block
    return None
