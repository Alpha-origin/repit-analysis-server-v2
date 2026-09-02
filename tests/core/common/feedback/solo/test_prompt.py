from app.core.common.feedback.solo.dto import AssembledSession
from app.core.common.feedback.solo.prompt import SYSTEM_PROMPT, build_grading_user_message


def test_grading_prompt_contains_type_and_tone_guidance() -> None:
    assembled = AssembledSession(targets=(), unanswered_question_ids=(), question_count=0)

    message = build_grading_user_message(assembled, "STRESS", 3000, persona_tone="PRESSURING")

    assert "성향(METICULOUS) 지침" in message
    assert "어조(PRESSURING) 지침" in message
    assert "점수는 바꾸지 마라" in SYSTEM_PROMPT
