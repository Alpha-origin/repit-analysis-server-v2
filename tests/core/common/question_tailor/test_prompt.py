from app.core.common.question_tailor.dto import CandidateProfile, OriginalQuestion
from app.core.common.question_tailor.prompt import SYSTEM_PROMPT, build_rewrite_user_message


def test_rewrite_prompt_contains_type_and_tone_guidance() -> None:
    profile = CandidateProfile(
        job_role="백엔드",
        experience_level="주니어",
        persona_type="METICULOUS",
        persona_tone="GENTLE",
    )
    question = OriginalQuestion(
        id=1,
        category="tech_choice",
        question="왜 Redis를 사용했나요?",
        expected_answer="캐시 선택 근거",
    )

    message = build_rewrite_user_message(profile, (question,), 800)

    assert "성향(METICULOUS) 지침" in message
    assert "어조(GENTLE) 지침" in message
    assert "검증 포인트" in SYSTEM_PROMPT


def test_tone_alone_is_a_valid_personalization_axis() -> None:
    profile = CandidateProfile(persona_tone="DIRECT")

    assert profile.has_any is True
