from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.common.applicant_profile.exploration import ProfileExploration
from app.core.common.feedback.multi.answer_grading import MultiAnswerGrading
from app.core.common.feedback.solo.answer_grading import AnswerGrading
from app.core.common.interview_qa.dto import Stage3Result
from app.core.common.interview_qa.stage4_file_reader import Stage4FileReader
from app.core.common.question_cycle.dto import CycleMode
from app.core.common.question_cycle.generate import QuestionCycleGenerate
from app.core.common.question_tailor.dto import CandidateProfile
from app.core.common.question_tailor.multi.generate import MultiQuestionGenerate
from app.core.common.question_tailor.rewrite import QuestionRewrite
from app.main.config import (
    ApplicantProfileSettings,
    FeedbackMultiSettings,
    FeedbackSoloSettings,
    InterviewQaSettings,
    QuestionCycleSettings,
    QuestionTailorMultiSettings,
    QuestionTailorSettings,
)
from tests.cycle_helpers import solo_submissions
from tests.multi_helpers import feedback_output, generated_entry
from tests.profile_helpers import raw_profile
from tests.regression.dev_roles import conftest
from tests.regression.dev_roles.conftest import (
    LEGACY,
    FeedbackInput,
    RecordingClient,
    TailorInput,
    assert_call,
    assert_golden,
    cycle_input,
    tool,
)


@pytest.mark.parametrize("major", ["BACKEND", "FRONTEND", None])
async def test_profile_model_boundary_and_forced_final_tool(
    major: str | None, profile_input: tuple[str, Stage3Result]
) -> None:
    settings = ApplicantProfileSettings(_env_file=None)
    qa = InterviewQaSettings(_env_file=None)
    responses = [[{"type": "text", "text": "조사 중"}] for _ in range(settings.MAX_TURNS)]
    client = RecordingClient([*responses, tool("submit_profile", raw_profile())])
    await ProfileExploration(
        client,
        Stage4FileReader(qa.MAX_FILE_BYTES, qa.MAX_FILES_PER_CALL),
        "stub",
        settings.MAX_TURNS,
        settings.TOKEN_LIMIT,
        settings.RESPONSE_MAX_TOKENS,
    ).execute(*profile_input, major)
    assert len(client.calls) == settings.MAX_TURNS + 1
    assert_call(f"profile__{major or 'none'}", client.calls[0], contract="profile")
    assert_golden("call__profile_final.json", {"tool_choice": client.calls[-1]["tool_choice"]})


@pytest.mark.parametrize("mode", ["SOLO", "MULTI"])
@pytest.mark.parametrize("partial", [False, True])
async def test_cycle_initial_and_retry_model_boundary(mode: CycleMode, partial: bool) -> None:
    request, valid = cycle_input(mode, partial)
    submissions = solo_submissions(valid) if mode == "SOLO" else [{"questions": valid}]
    client = RecordingClient(
        [
            tool("submit_question_cycle", {"questions": []}),
            *(tool("submit_question_cycle", submission, "fixed-retry") for submission in submissions),
        ]
    )
    settings = QuestionCycleSettings(_env_file=None)
    await QuestionCycleGenerate(
        client,
        "stub",
        settings.MAX_TOKENS,
        settings.RETRY,
        settings.EXPECTED_ANSWER_MAX_CHARS,
        settings.EXCLUDE_MAX,
        settings.TEXT_MAX_CHARS,
    ).execute(request)
    assert len(client.calls) == (4 if mode == "SOLO" else 2)
    category_set = "partial" if partial else "all"
    name = f"cycle__{mode}__{category_set}"
    contract = name if mode == "SOLO" else f"cycle__{category_set}"
    assert_call(name, client.calls[0], contract=contract)
    retry = client.calls[1]
    assert_golden(f"{name}__retry.json", {key: retry[key] for key in ("system", "messages", "tools", "tool_choice")})


@pytest.mark.parametrize("job_role", ["BACKEND", "FRONTEND"])
async def test_tailor_model_boundary(job_role: str, tailor_input: TailorInput) -> None:
    questions, _, _ = tailor_input
    client = RecordingClient(
        [
            tool(
                "submit_tailored_questions",
                {
                    "questions": [{"id": question.id, "question": "선택 이유는?"} for question in questions],
                },
            )
        ]
    )
    settings = QuestionTailorSettings(_env_file=None)
    result = await QuestionRewrite(
        client,
        "stub",
        settings.REWRITE_MAX_TOKENS,
        settings.QUESTION_MAX_CHARS,
        settings.SPOKEN_TEXT_MAX_CHARS,
    ).execute(CandidateProfile(job_role=job_role), questions)
    assert result is not None
    call = client.calls[0]
    legacy = json.loads((LEGACY / "rewrite_without_company.json").read_bytes())
    assert call["system"] == legacy["system"]
    assert_golden(f"tailor__{job_role}.txt", call["messages"][0]["content"])
    assert_call("tailor", call, prompt=False)


async def test_tailor_multi_tools_and_choice(tailor_input: TailorInput) -> None:
    questions, summary, personas = tailor_input
    client = RecordingClient(
        [tool("submit_generated_questions", {"questions": [generated_entry(1, i) for i in (0, 1)]})]
    )
    settings = QuestionTailorMultiSettings(_env_file=None)
    await MultiQuestionGenerate(
        client,
        "stub",
        settings.GENERATE_MAX_TOKENS,
        settings.TEXT_MAX_CHARS,
        settings.SPOKEN_TEXT_MAX_CHARS,
    ).execute(personas, summary, questions)
    legacy = json.loads((LEGACY / "generate_without_company.json").read_bytes())
    assert client.calls[0]["system"] == legacy["system"]
    assert_call("tailor_multi", client.calls[0], prompt=False)


@pytest.mark.parametrize("multi", [False, True])
async def test_feedback_model_boundary(multi: bool, feedback_input: FeedbackInput) -> None:
    assembled, personas = feedback_input
    name = "feedback_multi" if multi else "feedback_solo"
    client = RecordingClient([tool("submit_multi_feedback" if multi else "submit_feedback", feedback_output())])
    if multi:
        settings_multi = FeedbackMultiSettings(_env_file=None)
        await MultiAnswerGrading(
            client,
            "stub",
            settings_multi.GRADING_MAX_TOKENS,
            settings_multi.ANSWER_MAX_CHARS,
        ).execute(assembled, personas, {"q-1": personas[0]})
    else:
        settings_solo = FeedbackSoloSettings(_env_file=None)
        await AnswerGrading(
            client,
            "stub",
            settings_solo.GRADING_MAX_TOKENS,
            settings_solo.ANSWER_MAX_CHARS,
        ).execute(assembled, "REALISTIC", "DIRECT")
    assert_call(name, client.calls[0])


def test_golden_update_requires_explicit_switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conftest, "GOLDEN", tmp_path)
    monkeypatch.delenv("UPDATE_GOLDEN", raising=False)
    with pytest.raises(FileNotFoundError):
        assert_golden("new.txt", "changed")
    monkeypatch.setenv("UPDATE_GOLDEN", "1")
    assert_golden("new.txt", "original")
    monkeypatch.delenv("UPDATE_GOLDEN")
    with pytest.raises(AssertionError, match=r"(?s)---.*actual.*-original.*[+]changed"):
        assert_golden("new.txt", "changed")
    assert (tmp_path / "new.txt").read_bytes() == b"original\n"
