from _pytest.logging import LogCaptureFixture

from app.core.common.persona_guidance import build_persona_guidance


def test_legacy_persona_types_are_normalized() -> None:
    lines = build_persona_guidance("NEUTRAL", "DIRECT")

    assert lines[0].startswith("성향(REALISTIC)")
    assert lines[1].startswith("어조(DIRECT)")


def test_unknown_keys_use_default_guidance(caplog: LogCaptureFixture) -> None:
    lines = build_persona_guidance("UNKNOWN", "UNKNOWN_TONE")

    assert "기본 지침" in lines[0]
    assert "중립적이고 명확한 표현" in lines[1]
    assert caplog.records[0].message == "persona_guidance.unknown_key"
    assert caplog.records[1].message == "persona_guidance.unknown_key"
