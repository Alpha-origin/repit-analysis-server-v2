import pytest

from app.core.common.spoken_text import rules


def test_empty_glossary_does_not_add_an_empty_section() -> None:
    assert rules.GLOSSARY_LINES == ()
    block = rules.SPOKEN_TEXT_PROMPT_BLOCK
    assert "[용어 읽기]" not in block
    assert block == f"{rules._RULES}\n\n{rules._EXAMPLES}"
    assert block == block.strip()
    assert "\n\n\n" not in block
    assert "\n    " not in block
    assert all(line == line.rstrip() for line in block.splitlines())


def test_glossary_is_inserted_before_examples(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rules, "GLOSSARY_LINES", ("- Kafka: 카프카", "- Kubernetes: 쿠버네티스"))

    block = rules._build_block()

    assert "[용어 읽기]\n- Kafka: 카프카\n- Kubernetes: 쿠버네티스\n\n예시" in block
    assert block == block.strip()
    assert "\n    " not in block
    assert all(line == line.rstrip() for line in block.splitlines())
