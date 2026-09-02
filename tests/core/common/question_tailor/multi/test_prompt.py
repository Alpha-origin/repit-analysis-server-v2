from app.core.common.interview_qa.dto import ProjectSummary
from app.core.common.question_tailor.multi.dto import TailorPersona
from app.core.common.question_tailor.multi.prompt import build_generate_user_message


def test_multi_tailor_prompt_keeps_type_and_tone_separate() -> None:
    persona = TailorPersona(
        persona_id="hr-1",
        role="HR",
        style="REALISTIC",
        tone="DIRECT",
        question_count=1,
    )

    message = build_generate_user_message(
        (persona,),
        ProjectSummary(overview="프로젝트", repositories=[], core_features=[], tech_stack=[]),
        (),
        600,
    )

    assert "성향(REALISTIC) 지침" in message
    assert "어조(DIRECT) 지침" in message
