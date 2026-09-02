from app.core.common.feedback.multi.dto import FeedbackPersona
from app.core.common.feedback.multi.prompt import build_grading_user_message
from app.core.common.feedback.solo.dto import AssembledSession


def test_multi_feedback_prompt_includes_each_personas_tone() -> None:
    persona = FeedbackPersona(persona_id="p-1", role="HR", style="FRIENDLY", tone="GENTLE")
    assembled = AssembledSession(targets=(), unanswered_question_ids=(), question_count=0)

    message = build_grading_user_message(assembled, (persona,), {}, 3000)

    assert "성향(FRIENDLY) 지침" in message
    assert "어조(GENTLE) 지침" in message
