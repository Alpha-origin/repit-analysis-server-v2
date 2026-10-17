from __future__ import annotations

import copy
import difflib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.core.common.feedback.multi.dto import FeedbackPersona
from app.core.common.feedback.solo.dto import AssembledSession, GradingTarget
from app.core.common.interview_qa.dto import ProjectSummary, Stage3Result
from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicCallResult
from app.core.common.question_cycle.dto import CycleMode, QuestionCycleRequest
from app.core.common.question_tailor.dto import OriginalQuestion
from app.core.common.question_tailor.multi.dto import TailorPersona
from tests.cycle_helpers import ALL_CATEGORIES, entry, multi_entries, refined_profile, solo_entries
from tests.multi_helpers import feedback_body, tailor_body
from tests.profile_helpers import PATH_INDEX, raw_profile

GOLDEN = Path(__file__).parent / "golden"
LEGACY = Path(__file__).parents[2] / "core/common/question_tailor/fixtures"
TailorInput = tuple[tuple[OriginalQuestion, ...], ProjectSummary, tuple[TailorPersona, ...]]
FeedbackInput = tuple[AssembledSession, tuple[FeedbackPersona, ...]]


class RecordingClient:
    """Copy the actual model inputs without depending on company test clients or APIs."""

    def __init__(self, responses: list[list[dict[str, Any]]]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def call(self, **kwargs: Any) -> AnthropicCallResult:
        self.calls.append(copy.deepcopy(kwargs))
        return AnthropicCallResult(
            content_blocks=copy.deepcopy(self.responses[len(self.calls) - 1]),
            input_tokens=10,
            output_tokens=10,
            stop_reason="tool_use",
        )


def tool(name: str, value: dict[str, Any], tool_id: str = "fixed-tool") -> list[dict[str, Any]]:
    return [{"type": "tool_use", "id": tool_id, "name": name, "input": value}]


def assert_golden(name: str, value: str | dict[str, Any]) -> None:
    rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    rendered += "\n"
    path = GOLDEN / name
    if os.environ.get("UPDATE_GOLDEN") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(rendered.encode("utf-8"))
    expected = path.read_bytes().decode("utf-8")
    assert expected == rendered, "".join(
        difflib.unified_diff(expected.splitlines(True), rendered.splitlines(True), fromfile=str(path), tofile="actual")
    )


def assert_call(name: str, call: dict[str, Any], *, contract: str | None = None, prompt: bool = True) -> None:
    assert len(call["messages"]) == 1
    assert call["messages"][0]["role"] == "user"
    if prompt:
        content = call["messages"][0]["content"]
        assert isinstance(content, str)
        assert_golden(f"{name}.txt", call["system"] + "\n\n===== USER =====\n\n" + content)
    assert_golden(
        f"call__{contract or name}.json",
        {"tools": call["tools"], "tool_choice": call["tool_choice"]},
    )


@pytest.fixture(autouse=True)
def _default_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    prefixes = ("PROFILE_", "INTERVIEW_QA_", "QUESTION_TAILOR_", "QUESTION_CYCLE_", "FEEDBACK_SOLO_", "FEEDBACK_MULTI_")
    for key in tuple(os.environ):
        if key.startswith(prefixes):
            monkeypatch.delenv(key)


@pytest.fixture
def profile_input() -> tuple[str, Stage3Result]:
    portfolio = str(raw_profile()["overview"]) + "에서 재고 차감을 구현했습니다."
    return portfolio, Stage3Result(repos=[], tree_text="\n".join(sorted(PATH_INDEX)), path_index=PATH_INDEX)


def cycle_input(mode: CycleMode, partial: bool) -> tuple[QuestionCycleRequest, list[dict[str, Any]]]:
    categories = ALL_CATEGORIES[:2] if partial else ALL_CATEGORIES
    profile = refined_profile(("troubleshootings", "integrations", "structure_notes") if partial else ())
    valid = solo_entries(categories) if mode == "SOLO" else multi_entries()
    if mode == "MULTI" and partial:
        valid = [entry(set_no, category) for set_no in (1, 2, 3) for category in categories]
    return QuestionCycleRequest(
        profile=profile, mode=mode, exclude_questions=("예전 질문",), callback_url="https://example.com/callback"
    ), valid


@pytest.fixture
def tailor_input() -> TailorInput:
    body = tailor_body(count=2)
    questions = tuple(
        OriginalQuestion(
            id=question["id"],
            category=question["category"],
            question=question["question"],
            expected_answer=question["expectedAnswer"],
            based_on=question["basedOn"],
        )
        for question in body["questions"]
    )
    personas = tuple(
        TailorPersona(persona_id=persona["personaId"], role=persona["role"], question_count=2)
        for persona in body["otherPersonas"]
    )
    summary = ProjectSummary(
        overview=body["projectSummary"]["overview"], repositories=[], core_features=[], tech_stack=[]
    )
    return questions, summary, personas


@pytest.fixture
def feedback_input() -> FeedbackInput:
    body = feedback_body()
    question = body["questions"][0]
    target = GradingTarget(
        question_id=question["questionId"],
        type=question["type"],
        intention=question["intention"],
        content=question["content"],
        answer=body["answers"][0]["content"],
    )
    personas = tuple(
        FeedbackPersona(persona_id=persona["personaId"], role=persona["role"], style="REALISTIC", tone="DIRECT")
        for persona in body["personas"]
    )
    return AssembledSession(targets=(target,), unanswered_question_ids=(), question_count=1), personas
