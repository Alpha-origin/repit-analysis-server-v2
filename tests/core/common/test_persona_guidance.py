from typing import Any

from app.core.common.persona_guidance import (
    build_persona_guidance,
    build_question_persona_guidance,
)


def test_legacy_persona_types_are_normalized() -> None:
    lines = build_persona_guidance("NEUTRAL", "DIRECT")

    assert lines[0].startswith("성향(REALISTIC)")
    assert lines[1].startswith("어조(DIRECT)")


def test_unknown_keys_use_default_guidance(caplog: Any) -> None:
    lines = build_persona_guidance("UNKNOWN", "UNKNOWN_TONE")

    assert "기본 지침" in lines[0]
    assert "중립적이고 명확한 표현" in lines[1]
    assert caplog.records[0].message == "persona_guidance.unknown_key"
    assert caplog.records[1].message == "persona_guidance.unknown_key"


def test_question_guidance_uses_question_specific_instructions() -> None:
    lines = build_question_persona_guidance("FRIENDLY", "PRESSURING")

    assert "편안하게 이해할 수 있도록" in lines[0]
    assert "간결하고 단호하게 질문" in lines[1]
    assert "답변의 좋은 점을 인정" not in "\n".join(lines)


def test_question_guidance_does_not_assume_original_question() -> None:
    # N:1 신규 생성에도 쓰이므로 재작성 전용 전제('원질문')가 들어가면 안 된다.
    for persona_type in ("FRIENDLY", "REALISTIC", "METICULOUS"):
        for persona_tone in ("GENTLE", "DIRECT", "PRESSURING"):
            lines = build_question_persona_guidance(persona_type, persona_tone)
            assert "원질문" not in "\n".join(lines)
