from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from app.core.common.interview_qa.dto import Stage3Result
from app.core.common.interview_qa.errors import PipelineError
from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicCallResult
from app.core.common.interview_qa.stage4_file_reader import Stage4FileReader
from app.core.common.interview_qa.stage4_llm_session import Stage4LlmSession
from app.core.common.interview_qa.tools import GENERATE_RESULT_TOOL


class FakeAnthropicTextClient:
    def __init__(self, responses: list[AnthropicCallResult]) -> None:
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    async def call(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
        max_tokens: int = 4096,
    ) -> AnthropicCallResult:
        self.calls.append(
            {
                "model": model,
                "system": system,
                "messages": deepcopy(messages),
                "tools": tools,
                "tool_choice": tool_choice,
                "max_tokens": max_tokens,
            }
        )
        return self._responses.pop(0)


def _result_response(tool_id: str, tool_input: dict[str, Any]) -> AnthropicCallResult:
    return AnthropicCallResult(
        content_blocks=[
            {
                "type": "tool_use",
                "id": tool_id,
                "name": "generate_result",
                "input": tool_input,
            }
        ],
        input_tokens=100,
        output_tokens=50,
        stop_reason="tool_use",
    )


def _valid_result() -> dict[str, Any]:
    categories = [
        "tech_choice",
        "implementation",
        "troubleshooting",
        "integration",
        "structure",
    ]
    return {
        "project_summary": {
            "overview": "프로젝트 요약",
            "repositories": [],
            "core_features": [],
            "tech_stack": [],
        },
        "interview": [
            {
                "id": index,
                "category": category,
                "question": f"질문 {index}",
                "expected_answer": f"답변 {index}",
                "based_on": ["file_tree"],
            }
            for index, category in enumerate(categories, start=1)
        ],
    }


def _session(client: FakeAnthropicTextClient, *, max_turns: int = 3) -> Stage4LlmSession:
    return Stage4LlmSession(
        client=client,
        file_reader=Stage4FileReader(max_file_bytes=1_000, max_files_per_call=5),
        text_model="test-model",
        max_turns=max_turns,
        token_limit=10_000,
        response_max_tokens=4_096,
    )


def _repos_tree(*paths: str) -> Stage3Result:
    return Stage3Result(
        repos=[],
        tree_text="",
        path_index={path: f"/workspace/{path}" for path in paths},
    )


def test_generate_result_tool_uses_strict_pydantic_schema() -> None:
    assert GENERATE_RESULT_TOOL["strict"] is True

    schema = GENERATE_RESULT_TOOL["input_schema"]
    assert schema["additionalProperties"] is False
    assert all(definition["additionalProperties"] is False for definition in schema["$defs"].values())
    assert "based_on" in schema["$defs"]["CoreFeature"]["required"]


@pytest.mark.asyncio
async def test_regenerates_after_invalid_result() -> None:
    client = FakeAnthropicTextClient(
        [
            _result_response("tool-1", {}),
            _result_response("tool-2", _valid_result()),
        ]
    )

    result = await _session(client).execute("포트폴리오", _repos_tree())

    assert result == _valid_result()
    assert len(client.calls) == 2
    assert client.calls[1]["tool_choice"] == {"type": "tool", "name": "generate_result"}
    retry_message = client.calls[1]["messages"][-1]
    assert retry_message["role"] == "user"
    assert retry_message["content"][0]["type"] == "tool_result"
    assert retry_message["content"][0]["tool_use_id"] == "tool-1"
    assert retry_message["content"][0]["is_error"] is True


@pytest.mark.asyncio
async def test_succeeds_on_second_regeneration() -> None:
    client = FakeAnthropicTextClient(
        [
            _result_response("tool-1", {}),
            _result_response("tool-2", {}),
            _result_response("tool-3", _valid_result()),
        ]
    )

    result = await _session(client).execute("포트폴리오", _repos_tree())

    assert result == _valid_result()
    assert len(client.calls) == 3


@pytest.mark.asyncio
async def test_regenerates_when_question_ids_are_duplicated() -> None:
    invalid_result = deepcopy(_valid_result())
    invalid_result["interview"][1]["id"] = 1
    client = FakeAnthropicTextClient(
        [
            _result_response("tool-1", invalid_result),
            _result_response("tool-2", _valid_result()),
        ]
    )

    result = await _session(client).execute("포트폴리오", _repos_tree())

    assert result == _valid_result()
    assert len(client.calls) == 2
    retry_feedback = client.calls[1]["messages"][-1]["content"][0]["content"]
    assert "1부터 5까지 각각 한 번씩" in retry_feedback


@pytest.mark.asyncio
async def test_regenerates_when_question_is_blank() -> None:
    invalid_result = deepcopy(_valid_result())
    invalid_result["interview"][0]["question"] = "   "
    client = FakeAnthropicTextClient(
        [
            _result_response("tool-1", invalid_result),
            _result_response("tool-2", _valid_result()),
        ]
    )

    result = await _session(client).execute("포트폴리오", _repos_tree())

    assert result == _valid_result()
    assert len(client.calls) == 2
    retry_feedback = client.calls[1]["messages"][-1]["content"][0]["content"]
    assert "interview.0.question" in retry_feedback


@pytest.mark.asyncio
async def test_regenerates_when_evidence_path_is_not_in_file_tree() -> None:
    invalid_result = deepcopy(_valid_result())
    invalid_result["interview"][0]["based_on"] = ["repo/missing.py"]
    client = FakeAnthropicTextClient(
        [
            _result_response("tool-1", invalid_result),
            _result_response("tool-2", _valid_result()),
        ]
    )

    result = await _session(client).execute("포트폴리오", _repos_tree("repo/existing.py"))

    assert result == _valid_result()
    assert len(client.calls) == 2
    retry_feedback = client.calls[1]["messages"][-1]["content"][0]["content"]
    assert "파일 트리에 없는 근거 경로" in retry_feedback


@pytest.mark.asyncio
async def test_regenerates_invalid_result_after_max_turns() -> None:
    client = FakeAnthropicTextClient(
        [
            _result_response("tool-1", {}),
            _result_response("tool-2", _valid_result()),
        ]
    )

    result = await _session(client, max_turns=0).execute("포트폴리오", _repos_tree())

    assert result == _valid_result()
    assert len(client.calls) == 2
    assert client.calls[0]["tool_choice"] == {"type": "tool", "name": "generate_result"}
    assert client.calls[1]["tool_choice"] == {"type": "tool", "name": "generate_result"}


@pytest.mark.asyncio
async def test_returns_error_after_two_failed_regenerations() -> None:
    client = FakeAnthropicTextClient(
        [
            _result_response("tool-1", {}),
            _result_response("tool-2", {}),
            _result_response("tool-3", {}),
        ]
    )

    with pytest.raises(PipelineError) as exc_info:
        await _session(client).execute("포트폴리오", _repos_tree())

    assert exc_info.value.status_code == 500
    assert exc_info.value.message == "면접 질문 생성 결과가 형식을 충족하지 못했습니다."
    assert len(client.calls) == 3
