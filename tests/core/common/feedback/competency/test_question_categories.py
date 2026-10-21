import pytest
from tests.multi_helpers import generated_entry

from app.core.common.question_tailor.multi.dto import TailorPersona
from app.core.common.question_tailor.multi.generate import _parse_submission
from app.core.common.question_tailor.multi.tools import SUBMIT_GENERATED_QUESTIONS_TOOL


def test_question_category_schema_is_fixed() -> None:
    category = SUBMIT_GENERATED_QUESTIONS_TOOL["input_schema"]["properties"]["questions"]["items"]["properties"][
        "category"
    ]
    assert category["enum"] == ["motivation", "collaboration", "growth", "judgment", "ownership"]


def test_unknown_category_is_preserved_without_logging_content(caplog: pytest.LogCaptureFixture) -> None:
    entry = {**generated_entry(1), "category": "  Future_Category  "}
    blocks = [{"type": "tool_use", "name": "submit_generated_questions", "input": {"questions": [entry]}}]
    questions = _parse_submission(blocks, [TailorPersona(persona_id="hr", role="HR", question_count=1)], 600)
    assert questions[0].category == "future_category"
    assert "question_tailor_multi.generate.unknown_category" in caplog.text
    assert "Future_Category" not in caplog.text
