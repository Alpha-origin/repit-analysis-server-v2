from app.core.common.feedback.multi.dto import FeedbackPersona
from app.core.common.feedback.multi.prompt import (
    SYSTEM_PROMPT as MULTI_PROMPT,
    build_grading_user_message,
)
from app.core.common.feedback.rubric import AXIS_LEVEL_CRITERIA
from app.core.common.feedback.solo.dto import AssembledSession
from app.core.common.feedback.solo.prompt import SYSTEM_PROMPT as SOLO_PROMPT


def test_multi_feedback_prompt_includes_each_personas_tone() -> None:
    persona = FeedbackPersona(persona_id="p-1", role="HR", style="FRIENDLY", tone="GENTLE")
    assembled = AssembledSession(targets=(), unanswered_question_ids=(), question_count=0)

    message = build_grading_user_message(assembled, (persona,), {}, 3000)

    assert "성향(FRIENDLY) 지침" in message
    assert "어조(GENTLE) 지침" in message


def test_both_feedback_prompts_share_axis_level_criteria() -> None:
    assert AXIS_LEVEL_CRITERIA in SOLO_PROMPT
    assert AXIS_LEVEL_CRITERIA in MULTI_PROMPT
    # 점수는 서버가 계산한다. 예전 3지표를 모델에게 매기게 하는 지시가 남으면 안 된다.
    for prompt in (SOLO_PROMPT, MULTI_PROMPT):
        assert "total_score" not in prompt
        assert "{axis_criteria}" not in prompt
