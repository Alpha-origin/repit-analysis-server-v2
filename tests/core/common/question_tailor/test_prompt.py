from app.core.common.question_tailor.dto import CandidateProfile, OriginalQuestion
from app.core.common.question_tailor.prompt import SYSTEM_PROMPT, build_rewrite_user_message
from app.core.common.question_tailor.tools import SUBMIT_TAILORED_QUESTIONS_TOOL
from app.core.common.spoken_text.rules import SPOKEN_TEXT_PROMPT_BLOCK


def test_rewrite_requests_spoken_text_alongside_question() -> None:
    assert SYSTEM_PROMPT.endswith(SPOKEN_TEXT_PROMPT_BLOCK)
    schema = SUBMIT_TAILORED_QUESTIONS_TOOL["input_schema"]["properties"]["questions"]["items"]
    assert "spoken_text" in schema["required"]
    assert schema["properties"]["spoken_text"]["type"] == "string"


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


def test_intention_becomes_goal_and_expected_answer_is_reference() -> None:
    question = OriginalQuestion(
        id=1,
        category="tech_choice",
        question="왜 Redis를 사용했나요?",
        expected_answer="TTL 기반 캐시로 조회 부하를 줄였다",
        intention="캐시 저장소 선택 근거를 대안과 비교해 설명할 수 있는지",
    )

    message = build_rewrite_user_message(CandidateProfile(job_role="백엔드"), (question,), 800)

    assert "확인하려는 것: 캐시 저장소 선택 근거를 대안과 비교해 설명할 수 있는지" in message
    assert "참고 답안: TTL 기반 캐시로 조회 부하를 줄였다" in message


def test_legacy_question_without_intention_uses_expected_answer_as_goal() -> None:
    question = OriginalQuestion(
        id=1,
        category="tech_choice",
        question="왜 Redis를 사용했나요?",
        expected_answer="캐시 선택 근거",
    )

    message = build_rewrite_user_message(CandidateProfile(job_role="백엔드"), (question,), 800)

    assert "확인하려는 것: 캐시 선택 근거" in message
    assert "참고 답안" not in message
