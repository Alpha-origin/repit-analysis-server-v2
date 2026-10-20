from app.core.common.feedback.solo.dto import AssembledSession
from app.core.common.feedback.solo.prompt import SYSTEM_PROMPT, build_grading_user_message


def test_grading_prompt_contains_type_and_tone_guidance() -> None:
    assembled = AssembledSession(targets=(), unanswered_question_ids=(), question_count=0)

    message = build_grading_user_message(assembled, "STRESS", 3000, persona_tone="PRESSURING")

    assert "성향(METICULOUS) 지침" in message
    assert "어조(PRESSURING) 지침" in message
    assert "점수를 바꾸지 마라" in SYSTEM_PROMPT


def test_grading_prompt_bounds_job_title_in_user_message() -> None:
    assembled = AssembledSession(targets=(), unanswered_question_ids=(), question_count=0)

    message = build_grading_user_message(assembled, None, 3000, job_title="퍼포먼스 마케터")

    assert message.startswith("[면접 정보]\n<job_title>퍼포먼스 마케터</job_title>\n")
    assert "<job_title>" not in build_grading_user_message(assembled, None, 3000)
