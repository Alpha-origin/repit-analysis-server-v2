from typing import Any

import pytest

from app.core.common.interview_qa.dto import ProjectSummary
from app.core.common.interview_qa.errors import PipelineError
from app.core.common.question_tailor.multi.dto import TailorPersona
from app.core.common.question_tailor.multi.generate import MultiQuestionGenerate, _parse_entry
from tests.multi_helpers import StubTextClient, generated_entry


@pytest.mark.parametrize("missing_index", [None, 3, 99])
async def test_persona_mapping_order_excess_and_missing(missing_index: int | None) -> None:
    personas = tuple(
        TailorPersona(persona_id=f"real-{index}", role=role, question_count=count)
        for index, (role, count) in enumerate((("HR", 1), ("CEO", 3), ("PM", 2)), 1)
    )
    entries = [generated_entry(index, number) for index in (3, 2, 1) for number in range(4)]
    if missing_index == 3:
        entries = [entry for entry in entries if entry["persona_index"] != 3]
    elif missing_index == 99:
        for entry in entries:
            if entry["persona_index"] == 3:
                entry["persona_index"] = 99
    else:
        entries.append(generated_entry(99))
    client = StubTextClient({"submit_generated_questions": {"questions": entries}})
    stage = MultiQuestionGenerate(client, "stub", 4608, 600, 600)
    if missing_index is not None:
        with pytest.raises(PipelineError) as exc:
            await stage.execute(
                personas, ProjectSummary(overview="요약", repositories=[], core_features=[], tech_stack=[]), ()
            )
        assert exc.value.status_code == 500
    else:
        result = await stage.execute(
            personas, ProjectSummary(overview="요약", repositories=[], core_features=[], tech_stack=[]), ()
        )
        assert [q.persona_id for q in result] == ["real-1", "real-2", "real-2", "real-2", "real-3", "real-3"]
        assert [q.question for q in result] == ["질문 1-0", "질문 2-0", "질문 2-1", "질문 2-2", "질문 3-0", "질문 3-1"]


@pytest.mark.parametrize("spoken", [" 발음 문장 ", None, "", "  ", 3, [], "가" * 601])
async def test_invalid_spoken_text_does_not_fail_generation(spoken: object) -> None:
    entries = [generated_entry(1, number) for number in range(2)]
    entries[0]["spoken_text"] = spoken
    client = StubTextClient({"submit_generated_questions": {"questions": entries}})
    stage = MultiQuestionGenerate(client, "stub", 4608, 600, 600)
    personas = (TailorPersona(persona_id="hr-1", role="HR", question_count=2),)

    result = await stage.execute(
        personas, ProjectSummary(overview="요약", repositories=[], core_features=[], tech_stack=[]), ()
    )

    assert len(result) == 2
    assert result[0].spoken_text == ("발음 문장" if spoken == " 발음 문장 " else None)
    assert result[1].spoken_text is None


@pytest.mark.parametrize("length", [250, 251])
def test_truncated_question_discards_spoken_text(length: int) -> None:
    entry = generated_entry(1)
    entry["question"] = f" {'가' * length} "
    entry["spoken_text"] = "대응하는 발음 문장"
    personas = (TailorPersona(persona_id="hr-1", role="HR", question_count=1),)

    parsed = _parse_entry(entry, personas, 600)

    assert parsed is not None
    _, question = parsed
    assert question.question == "가" * 250
    assert question.spoken_text == ("대응하는 발음 문장" if length == 250 else None)


@pytest.mark.parametrize("field", ["question", "intention", "expected_answer", "category"])
@pytest.mark.parametrize("invalid", [None, "", "  ", 7])
def test_incomplete_required_fields_discard_entry(field: str, invalid: object) -> None:
    entry: dict[str, Any] = generated_entry(1)
    if invalid is None:
        entry.pop(field)
    else:
        entry[field] = invalid
    entry["spoken_text"] = "발음 문장"
    personas = (TailorPersona(persona_id="hr-1", role="HR", question_count=1),)

    assert _parse_entry(entry, personas, 600) is None


async def test_generation_passes_configured_spoken_text_limit_to_parser() -> None:
    entry = generated_entry(1)
    entry["spoken_text"] = "가" * 4
    client = StubTextClient({"submit_generated_questions": {"questions": [entry]}})
    stage = MultiQuestionGenerate(client, "stub", 4608, 600, spoken_text_max_chars=3)

    result = await stage.execute(
        (TailorPersona(persona_id="hr-1", role="HR", question_count=1),),
        ProjectSummary(overview="요약", repositories=[], core_features=[], tech_stack=[]),
        (),
    )

    assert result[0].spoken_text is None
