from unittest.mock import AsyncMock

import pytest

from app.core.commands.dispatch_question_tailor import DispatchQuestionTailor
from app.core.common.question_tailor.dto import CandidateProfile, OriginalQuestion, QuestionTailorRequest
from app.core.common.question_tailor.rewrite import QuestionRewrite
from tests.multi_helpers import RecordingWebhook, StubTextClient


@pytest.mark.parametrize("mode", ["spoken", "missing_spoken", "fallback"])
async def test_success_callback_and_original_fallback(mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    entries = [
        {"id": 2, "question": "Redis를 선택한 이유는 무엇인가요?"},
        {"id": 1, "question": "JWT를 선택한 이유는 무엇인가요?"},
    ]
    if mode == "spoken":
        entries[0]["spoken_text"] = "레디스를 선택한 이유는 무엇인가요?"
        entries[1]["spoken_text"] = "제이더블유티를 선택한 이유는 무엇인가요?"
    elif mode == "fallback":
        entries.pop()
    llm = StubTextClient({"submit_tailored_questions": {"questions": entries}})
    call = AsyncMock(wraps=llm.call)
    monkeypatch.setattr(llm, "call", call)
    webhook = RecordingWebhook()
    dispatcher = DispatchQuestionTailor(webhook, QuestionRewrite(llm, "stub", 3072, 800, 600))
    request = QuestionTailorRequest(
        interview_id="iv-1",
        user_id="u-1",
        profile=CandidateProfile(job_role="백엔드"),
        questions=tuple(
            OriginalQuestion(id=value, category="tech_choice", question=f"원질문 {value}", expected_answer="선택 근거")
            for value in (1, 2)
        ),
        callback_url="https://example.com/callback",
    )

    await dispatcher.execute("job-1", request)

    (payload,) = webhook.payloads
    assert payload["status"] == "succeeded"
    assert payload["jobId"] == "job-1"
    assert payload["interviewId"] == "iv-1"
    assert payload["result"]["tailored"] is (mode != "fallback")
    questions = payload["result"]["questions"]
    assert [q["id"] for q in questions] == [1, 2]
    assert all(set(q) == {"id", "question", "spokenText"} for q in questions)
    if mode == "spoken":
        assert [q["spokenText"] for q in questions] == [
            "제이더블유티를 선택한 이유는 무엇인가요?",
            "레디스를 선택한 이유는 무엇인가요?",
        ]
    else:
        assert all(q["spokenText"] is None for q in questions)
    assert [q["question"] for q in questions] == (
        ["원질문 1", "원질문 2"] if mode == "fallback" else [entries[1]["question"], entries[0]["question"]]
    )
    # 폴백이나 발음 문장 누락 시 추가 LLM 호출은 하지 않는다.
    assert call.await_count == 1
