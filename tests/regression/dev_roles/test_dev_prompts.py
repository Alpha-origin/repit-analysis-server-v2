from __future__ import annotations

from pathlib import Path

import pytest

from app.core.common.applicant_profile.exploration import ProfileExploration
from app.core.common.feedback.multi.answer_grading import MultiAnswerGrading
from app.core.common.feedback.multi.dto import FeedbackPersona
from app.core.common.feedback.solo.answer_grading import AnswerGrading
from app.core.common.feedback.solo.dto import AssembledSession, GradingTarget
from app.core.common.interview_qa.dto import ProjectSummary, Stage3Result
from app.core.common.interview_qa.stage4_file_reader import Stage4FileReader
from app.core.common.question_cycle.dto import CycleMode, QuestionCycleRequest
from app.core.common.question_cycle.generate import QuestionCycleGenerate
from app.core.common.question_tailor.dto import CandidateProfile, OriginalQuestion
from app.core.common.question_tailor.multi.dto import TailorPersona
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
from tests.cycle_helpers import ALL_CATEGORIES, entry, multi_entries, refined_profile, solo_entries
from tests.multi_helpers import feedback_output, generated_entry
from tests.regression.dev_roles import conftest
from tests.regression.dev_roles.conftest import RecordingClient, assert_call, assert_golden, tool


@pytest.mark.parametrize("major", ["BACKEND", "FRONTEND", None])
async def test_profile_model_boundary_and_forced_final_tool(major: str | None) -> None:
    client = RecordingClient([[{"type": "text", "text": "조사 중"}], tool("submit_profile", {"overview": "o"})])
    settings = ApplicantProfileSettings(_env_file=None)
    qa = InterviewQaSettings(_env_file=None)
    await ProfileExploration(
        client,
        Stage4FileReader(qa.MAX_FILE_BYTES, qa.MAX_FILES_PER_CALL),
        "stub",
        1,
        settings.TOKEN_LIMIT,
        settings.RESPONSE_MAX_TOKENS,
    ).execute(
        "주문 서비스에서 재고 차감을 구현했습니다.",
        Stage3Result(repos=[], tree_text="app/Main.java", path_index={}),
        major,
    )
    assert_call(f"profile__{major or 'none'}", client.calls[0])
    assert_golden("call__profile_final.json", {"tool_choice": client.calls[-1]["tool_choice"]})


@pytest.mark.parametrize("mode", ["SOLO", "MULTI"])
@pytest.mark.parametrize("partial", [False, True])
async def test_cycle_initial_and_retry_model_boundary(mode: CycleMode, partial: bool) -> None:
    categories = ALL_CATEGORIES[:2] if partial else ALL_CATEGORIES
    profile = refined_profile(("troubleshootings", "integrations", "structure_notes") if partial else ())
    valid = solo_entries(categories) if mode == "SOLO" else multi_entries()
    if mode == "MULTI" and partial:
        valid = [entry(set_no, category) for set_no in (1, 2, 3) for category in categories]
    client = RecordingClient(
        [
            tool("submit_question_cycle", {"questions": []}),
            tool("submit_question_cycle", {"questions": valid}, "fixed-retry"),
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
    ).execute(QuestionCycleRequest(profile=profile, mode=mode, callback_url="https://example.com/callback"))
    name = f"cycle__{mode}__{'partial' if partial else 'all'}"
    assert_call(name, client.calls[0])
    assert_golden(f"{name}__retry.json", client.calls[1])


@pytest.mark.parametrize("job_role", ["BACKEND", "FRONTEND"])
async def test_tailor_model_boundary(job_role: str) -> None:
    question = OriginalQuestion(id=1, category="tech_choice", question="왜 선택했나요?", expected_answer="선택 근거")
    client = RecordingClient(
        [tool("submit_tailored_questions", {"questions": [{"id": 1, "question": "선택 이유는?"}]})]
    )
    settings = QuestionTailorSettings(_env_file=None)
    result = await QuestionRewrite(
        client,
        "stub",
        settings.REWRITE_MAX_TOKENS,
        settings.QUESTION_MAX_CHARS,
        settings.SPOKEN_TEXT_MAX_CHARS,
    ).execute(CandidateProfile(job_role=job_role), (question,))
    assert result is not None
    assert_call(f"tailor__{job_role}", client.calls[0])


async def test_tailor_multi_tools_and_choice() -> None:
    client = RecordingClient([tool("submit_generated_questions", {"questions": [generated_entry(1)]})])
    settings = QuestionTailorMultiSettings(_env_file=None)
    await MultiQuestionGenerate(
        client,
        "stub",
        settings.GENERATE_MAX_TOKENS,
        settings.TEXT_MAX_CHARS,
        settings.SPOKEN_TEXT_MAX_CHARS,
    ).execute(
        (TailorPersona(persona_id="p-1", role="HR", question_count=1),),
        ProjectSummary(overview="주문 서비스", repositories=[], core_features=[], tech_stack=[]),
        (),
    )
    # Existing #30 fixtures already freeze the no-company prompt and user message.
    assert_call("tailor_multi", client.calls[0], prompt=False)


@pytest.mark.parametrize("multi", [False, True])
async def test_feedback_model_boundary(multi: bool) -> None:
    target = GradingTarget(
        question_id="q-1",
        type="ORIGINAL",
        intention="선택 근거",
        content="왜 선택했나요?",
        answer="성능 요구 때문에 선택했습니다. 동기 방식보다 대기 시간이 짧았습니다.",
    )
    assembled = AssembledSession(targets=(target,), unanswered_question_ids=(), question_count=1)
    name = "feedback_multi" if multi else "feedback_solo"
    client = RecordingClient([tool("submit_multi_feedback" if multi else "submit_feedback", feedback_output())])
    if multi:
        settings_multi = FeedbackMultiSettings(_env_file=None)
        personas = (FeedbackPersona(persona_id="p-0", role="TECH"),)
        await MultiAnswerGrading(
            client, "stub", settings_multi.GRADING_MAX_TOKENS, settings_multi.ANSWER_MAX_CHARS
        ).execute(
            assembled,
            personas,
            {"q-1": personas[0]},
        )
    else:
        settings_solo = FeedbackSoloSettings(_env_file=None)
        await AnswerGrading(client, "stub", settings_solo.GRADING_MAX_TOKENS, settings_solo.ANSWER_MAX_CHARS).execute(
            assembled,
            "REALISTIC",
            "DIRECT",
        )
    assert_call(name, client.calls[0])


def test_golden_update_requires_explicit_switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conftest, "GOLDEN", tmp_path)
    monkeypatch.delenv("UPDATE_GOLDEN", raising=False)
    with pytest.raises(FileNotFoundError):
        assert_golden("new.txt", "changed")
