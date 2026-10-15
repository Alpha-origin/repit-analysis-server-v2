"""깊이 축은 모델이 추출한 원문 근거를 확인한 뒤 서버가 등급을 결정한다."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

_DESCRIPTIONS = {
    "reason": "특정 문제·제약·목표와 선택을 연결한 이유의 원문 인용. 없으면 빈 문자열.",
    "alternatives": "둘 이상의 대안을 실제로 비교한 차이·선택 기준의 원문 인용. 없으면 빈 문자열.",
    "limitation": "선택한 방식의 구체적인 한계·적용 조건의 원문 인용. 없으면 빈 문자열.",
    "generic_reason": "'좋아서' 같은 일반적인 이유만 있는 경우 그 원문 인용. 없으면 빈 문자열.",
}

DEPTH_EVIDENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "description": "깊이 등급 대신 실제 답변의 최소한의 원문 근거만 추출한다. 없는 근거는 빈 문자열.",
    "properties": {
        key: {"type": "string", "description": description}
        for key, description in _DESCRIPTIONS.items()
    },
    "required": list(_DESCRIPTIONS),
}


def evidence_sources(answer: str, parent_answer: str | None, max_chars: int) -> tuple[str, ...]:
    """모델에 실제로 전달된 답변 범위만 인용 검증에 허용한다."""
    return tuple(text[:max_chars] for text in (answer, parent_answer) if text)


def depth_level_from_evidence(raw: object, sources: Sequence[str]) -> int | None:
    """원문에 없는 인용·잘못된 형식은 실패시킨다. 의미 분류 자체는 모델의 책임이다."""
    if not isinstance(raw, dict) or set(raw) != set(_DESCRIPTIONS):
        return None
    normalized_sources = [" ".join(source.split()) for source in sources]
    present: dict[str, bool] = {}
    for key in _DESCRIPTIONS:
        quote = raw[key]
        if not isinstance(quote, str):
            return None
        quote = " ".join(quote.split())
        if quote and not any(quote in source for source in normalized_sources):
            return None
        present[key] = bool(quote)

    if present["reason"]:
        return 2 + int(present["alternatives"]) + int(present["limitation"])
    return 1 if present["generic_reason"] else 0
