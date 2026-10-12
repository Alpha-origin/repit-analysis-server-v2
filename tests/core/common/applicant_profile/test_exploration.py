from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from app.core.common.applicant_profile.exploration import ProfileExploration
from app.core.common.interview_qa.dto import RepoTree, Stage3Result
from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicCallResult
from app.core.common.interview_qa.stage4_file_reader import Stage4FileReader
from app.core.common.interview_qa.stage4_llm_session import Stage4LlmSession


class ScriptedClient:
    """정해 둔 응답을 차례로 돌려주고, 받은 호출 인자를 남긴다."""

    def __init__(self, responses: list[list[dict[str, Any]]]) -> None:
        self.responses = responses
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
        self.calls.append({"system": system, "messages": list(messages), "tools": tools, "tool_choice": tool_choice})
        return AnthropicCallResult(
            content_blocks=self.responses[len(self.calls) - 1],
            input_tokens=10,
            output_tokens=10,
            stop_reason="tool_use",
        )


def _tool_use(name: str, tool_input: dict[str, Any], tool_id: str = "t-1") -> list[dict[str, Any]]:
    return [{"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}]


def _repos_tree(tmp_path: Path) -> Stage3Result:
    source = tmp_path / "Main.java"
    source.write_text("class Main {}")
    return Stage3Result(
        repos=[RepoTree(name="app", role="api_server", file_paths=["Main.java"])],
        tree_text="app/Main.java",
        path_index={"app/Main.java": str(source)},
    )


def _reader() -> Stage4FileReader:
    return Stage4FileReader(max_file_bytes=60_000, max_files_per_call=12)


async def test_profile_exploration_reads_files_then_returns_submit_profile_input(tmp_path: Path) -> None:
    client = ScriptedClient(
        [
            _tool_use("read_files", {"paths": ["app/Main.java"]}),
            _tool_use("submit_profile", {"overview": "o"}, "t-2"),
        ]
    )
    exploration = ProfileExploration(client, _reader(), "stub", 8, 100_000, 8192)

    result = await exploration.execute("포트폴리오", _repos_tree(tmp_path), major="컴퓨터공학")

    assert result == {"overview": "o"}
    first = client.calls[0]
    assert "[지원자 전공]\n컴퓨터공학" in first["messages"][0]["content"]
    assert [tool["name"] for tool in first["tools"]] == ["read_files", "submit_profile"]
    # read_files 결과가 다음 호출에 tool_result 로 실린다.
    tool_result = client.calls[1]["messages"][-1]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert "class Main" in tool_result["content"]


async def test_profile_exploration_without_major_omits_major_section(tmp_path: Path) -> None:
    client = ScriptedClient([_tool_use("submit_profile", {"overview": "o"})])
    exploration = ProfileExploration(client, _reader(), "stub", 8, 100_000, 8192)

    await exploration.execute("포트폴리오", _repos_tree(tmp_path), major=None)

    assert "[지원자 전공]" not in client.calls[0]["messages"][0]["content"]


async def _run_profile(client: ScriptedClient, tree: Stage3Result) -> dict[str, Any]:
    return await ProfileExploration(client, _reader(), "stub", 1, 100_000, 8192).execute("p", tree, major=None)


async def _run_generate(client: ScriptedClient, tree: Stage3Result) -> dict[str, Any]:
    return await Stage4LlmSession(client, _reader(), "stub", 1, 100_000, 4096).execute("p", tree)


@pytest.mark.parametrize(
    ("run", "final_tool"),
    [(_run_profile, "submit_profile"), (_run_generate, "generate_result")],
)
async def test_max_turns_forces_the_final_tool_of_each_spec(
    tmp_path: Path,
    run: Callable[[ScriptedClient, Stage3Result], Awaitable[dict[str, Any]]],
    final_tool: str,
) -> None:
    # 루프를 다 쓰면 각 엔드포인트의 종료 도구로 강제 호출한다. /generate 동작이 바뀌지 않았는지도 함께 본다.
    client = ScriptedClient([[{"type": "text", "text": "생각 중"}], _tool_use(final_tool, {"ok": True})])

    result = await run(client, _repos_tree(tmp_path))

    assert result == {"ok": True}
    assert client.calls[-1]["tool_choice"] == {"type": "tool", "name": final_tool}
    assert final_tool in client.calls[-1]["messages"][-1]["content"]
