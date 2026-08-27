from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError

from app.core.common.interview_qa.dto import InterviewQaResult, Stage3Result
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
_RESULT_FORMAT_FAILURE_MESSAGE = "면접 질문 생성 결과가 형식을 충족하지 못했습니다."
_GENERATE_RESULT_TOOL_NAME = "generate_result"
_MAX_RESULT_RETRIES = 2


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
        self._client = client
        self._reader = file_reader
        self._model = text_model
        self._max_turns = max_turns
        self._token_limit = token_limit
        self._response_max_tokens = response_max_tokens

    async def execute(self, portfolio_text: str, repos_tree: Stage3Result) -> dict[str, Any]:
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
                validated_result, validation_error = _validate_generate_result(tool_input)
                regeneration_retries = 0
                if validation_error is not None:
                    self._log_result_validation_failure(retry_count=0, error=validation_error)
                    (
                        validated_result,
                        retry_input_tokens,
                        retry_output_tokens,
                        regeneration_retries,
                    ) = await self._regenerate_invalid_result(
                        messages=messages,
                        failed_tool_id=tool_id,
                        validation_error=validation_error,
                    )
                    total_input_tokens += retry_input_tokens
                    total_output_tokens += retry_output_tokens

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
                        "regeneration_retries": regeneration_retries,
                    },
                )
                return validated_result

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
        forced, forced_input_tokens, forced_output_tokens, regeneration_retries = await self._force_generate(messages)
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
                "regeneration_retries": regeneration_retries,
            },
        )
        return forced

    # ---------------- 내부 ----------------

    async def _force_generate(self, messages: list[dict[str, Any]]) -> tuple[dict[str, Any], int, int, int]:
        messages.append(
            {
                "role": "user",
                "content": ("추가 파일을 읽지 말고, 지금까지의 분석만으로 generate_result 를 호출하라."),
            }
        )
        response = await self._safe_call(
            messages,
            tool_choice={"type": "tool", "name": _GENERATE_RESULT_TOOL_NAME},
        )
        messages.append({"role": "assistant", "content": response.content_blocks})
        tool_use = _first_tool_use(response.content_blocks)
        validated_result, validation_error = _validate_generate_tool_use(tool_use)
        if validation_error is None:
            return validated_result, response.input_tokens, response.output_tokens, 0

        self._log_result_validation_failure(retry_count=0, error=validation_error)
        failed_tool_id = str(tool_use.get("id", "")) if tool_use is not None else ""
        regenerated, retry_input_tokens, retry_output_tokens, retry_count = await self._regenerate_invalid_result(
            messages=messages,
            failed_tool_id=failed_tool_id,
            validation_error=validation_error,
        )
        return (
            regenerated,
            response.input_tokens + retry_input_tokens,
            response.output_tokens + retry_output_tokens,
            retry_count,
        )

    async def _regenerate_invalid_result(
        self,
        *,
        messages: list[dict[str, Any]],
        failed_tool_id: str,
        validation_error: str,
    ) -> tuple[dict[str, Any], int, int, int]:
        total_input_tokens = 0
        total_output_tokens = 0
        current_tool_id = failed_tool_id
        current_error = validation_error

        for retry_count in range(1, _MAX_RESULT_RETRIES + 1):
            messages.append(_build_regeneration_feedback(current_tool_id, current_error))
            response = await self._safe_call(
                messages,
                tool_choice={"type": "tool", "name": _GENERATE_RESULT_TOOL_NAME},
            )
            total_input_tokens += response.input_tokens
            total_output_tokens += response.output_tokens
            messages.append({"role": "assistant", "content": response.content_blocks})

            tool_use = _first_tool_use(response.content_blocks)
            validated_result, next_error = _validate_generate_tool_use(tool_use)
            if next_error is None:
                logger.info(
                    "stage4_llm_session.result_regenerated",
                    extra={"retry_count": retry_count, "max_retries": _MAX_RESULT_RETRIES},
                )
                return validated_result, total_input_tokens, total_output_tokens, retry_count

            self._log_result_validation_failure(retry_count=retry_count, error=next_error)
            current_tool_id = str(tool_use.get("id", "")) if tool_use is not None else ""
            current_error = next_error

        raise PipelineError(500, _RESULT_FORMAT_FAILURE_MESSAGE)

    @staticmethod
    def _log_result_validation_failure(*, retry_count: int, error: str) -> None:
        logger.warning(
            "stage4_llm_session.result_validation_failed retry_count=%s max_retries=%s error=%s",
            retry_count,
            _MAX_RESULT_RETRIES,
            error,
            extra={
                "retry_count": retry_count,
                "max_retries": _MAX_RESULT_RETRIES,
                "error": error,
            },
        )

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
    for block in content_blocks:
        if block.get("type") == "tool_use":
            return block
    return None


def _validate_generate_result(tool_input: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    try:
        validated = InterviewQaResult.model_validate(tool_input)
    except ValidationError as exc:
        return {}, _summarize_validation_error(exc)
    return validated.model_dump(), None


def _validate_generate_tool_use(tool_use: dict[str, Any] | None) -> tuple[dict[str, Any], str | None]:
    if tool_use is None:
        return {}, "generate_result 도구 호출이 없습니다."
    if tool_use.get("name") != _GENERATE_RESULT_TOOL_NAME:
        return {}, f"generate_result 대신 {tool_use.get('name')!r} 도구가 호출됐습니다."

    tool_input = tool_use.get("input")
    if not isinstance(tool_input, dict):
        return {}, "generate_result 입력이 JSON 객체가 아닙니다."
    return _validate_generate_result(tool_input)


def _summarize_validation_error(exc: ValidationError) -> str:
    issues: list[dict[str, str]] = []
    for error in exc.errors(include_url=False, include_input=False):
        path = ".".join(str(part) for part in error["loc"])
        issues.append(
            {
                "path": path or "root",
                "message": str(error["msg"]),
                "type": str(error["type"]),
            }
        )
    return json.dumps(issues, ensure_ascii=False)


def _build_regeneration_feedback(tool_use_id: str, validation_error: str) -> dict[str, Any]:
    feedback = (
        "generate_result 입력이 질문 조건을 충족하지 못했습니다. "
        "아래 검증 오류를 모두 수정하여 전체 결과를 다시 생성하세요. "
        "기존 결과의 일부만 보내지 말고 generate_result를 다시 호출하세요.\n"
        f"검증 오류: {validation_error}"
    )
    if not tool_use_id:
        return {"role": "user", "content": feedback}
    return {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": feedback,
                "is_error": True,
            }
        ],
    }
