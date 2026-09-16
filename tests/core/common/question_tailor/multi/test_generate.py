import pytest

from app.core.common.interview_qa.dto import ProjectSummary
from app.core.common.interview_qa.errors import PipelineError
from app.core.common.question_tailor.multi.dto import TailorPersona
from app.core.common.question_tailor.multi.generate import MultiQuestionGenerate
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
    stage = MultiQuestionGenerate(client, "stub", 3072, 600)
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
