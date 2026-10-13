from typing import Any

import pytest

from app.core.common.question_tailor.dto import CandidateProfile, OriginalQuestion
from app.core.common.question_tailor.rewrite import QuestionRewrite, _parse_submission
from tests.multi_helpers import StubTextClient


def _blocks(entries: list[object]) -> list[dict[str, Any]]:
    return [{"type": "tool_use", "name": "submit_tailored_questions", "input": {"questions": entries}}]


@pytest.mark.parametrize("spoken_ids", [(1, 2), (1,), ()])
def test_optional_spoken_text_keeps_questions_and_request_order(
    spoken_ids: tuple[int, ...], caplog: pytest.LogCaptureFixture
) -> None:
    entries: list[object] = [
        {"id": value, "question": f" 질문 {value} ", "spoken_text": f" 발음 {value} "}
        if value in spoken_ids
        else {"id": value, "question": f" 질문 {value} "}
        for value in (2, 1)
    ]

    result = _parse_submission(_blocks(entries), (1, 2), 600)

    assert result is not None
    assert [q.id for q in result] == [1, 2]
    assert [q.question for q in result] == ["질문 1", "질문 2"]
    assert [q.spoken_text for q in result] == [f"발음 {value}" if value in spoken_ids else None for value in (1, 2)]
    warnings = [record for record in caplog.records if record.message == "question_tailor.rewrite.spoken_text_missing"]
    if len(spoken_ids) < 2:
        assert len(warnings) == 1
        assert warnings[0].__dict__["missing_count"] == 2 - len(spoken_ids)
    else:
        assert not warnings


@pytest.mark.parametrize("spoken", [None, "", "  \n ", 3, {}, "가" * 601])
def test_invalid_spoken_text_does_not_fail_rewrite(spoken: object) -> None:
    result = _parse_submission(_blocks([{"id": 1, "question": "질문", "spoken_text": spoken}]), (1,), 600)

    assert result is not None
    assert result[0].question == "질문"
    assert result[0].spoken_text is None


@pytest.mark.parametrize("entry", [{"id": 2}, {"id": 2, "question": "  "}, {"id": 2, "question": 7}])
def test_missing_question_still_fails_entire_rewrite(entry: dict[str, Any]) -> None:
    entry["spoken_text"] = "발음 문장이 있어도 질문이 없으면 실패"
    blocks = _blocks([{"id": 1, "question": "질문 1", "spoken_text": "발음 1"}, entry])

    assert _parse_submission(blocks, (1, 2), 600) is None


def test_unknown_ids_and_malformed_entries_are_ignored() -> None:
    blocks = _blocks(
        [None, "invalid", {"id": 99, "question": "추가 질문"}, {"id": 7, "question": "질문", "spoken_text": "발음"}]
    )

    result = _parse_submission(blocks, (7,), 600)

    assert result is not None
    assert len(result) == 1
    assert result[0].id == 7
    assert result[0].spoken_text == "발음"


async def test_rewrite_passes_configured_spoken_text_limit_to_parser() -> None:
    client = StubTextClient(
        {"submit_tailored_questions": {"questions": [{"id": 1, "question": "질문", "spoken_text": "가" * 4}]}}
    )
    rewrite = QuestionRewrite(client, "stub", 3072, 800, spoken_text_max_chars=3)
    question = OriginalQuestion(id=1, category="tech_choice", question="원질문", expected_answer="선택 근거")

    result = await rewrite.execute(CandidateProfile(job_role="백엔드"), (question,))

    assert result is not None
    assert result[0].spoken_text is None
