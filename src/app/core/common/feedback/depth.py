"""깊이 축은 모델이 추출한 원문 근거를 확인한 뒤 서버가 등급을 결정한다."""

from __future__ import annotations

import re
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
    "properties": {key: {"type": "string", "description": description} for key, description in _DESCRIPTIONS.items()},
    "required": list(_DESCRIPTIONS),
}


def evidence_sources(answer: str, parent_answer: str | None, max_chars: int) -> tuple[str, ...]:
    """모델에 실제로 전달된 답변 범위만 인용 검증에 허용한다."""
    return tuple(text[:max_chars] for text in (answer, parent_answer) if text)


# 모델은 떨어진 두 문장을 한 인용으로 잇는 경우가 많다(이슈 #46). 문장 하나하나가 원문에 같은 순서로 있으면 인정한다.
# 마침표는 뒤에 공백·끝이 올 때만 문장 끝으로 본다. "3.5배"·"Node.js"를 쪼개면 지어낸 숫자가 통과한다.
_SENTENCE_END = re.compile(r"…+|\.{2,}|[.!?。](?=\s|$)")
_EDGE_CHARS = " \t\n\"'()[]{}<>「」『』,·"
# 이어 붙인 인용의 조각마다 요구하는 최소 글자 수(공백 제외). 가정: 8자.
# "성능…선택"처럼 짧은 낱말을 이어 붙여 답변에 없던 이유를 만드는 것을 막는다. 한 덩어리 인용에는 적용하지 않는다.
MIN_JOINED_SEGMENT_CHARS = 8
# 곱은 따옴표(U+201C·201D·201E·2018·2019)를 곧은 따옴표로 맞춘다.
_QUOTE_MARKS = str.maketrans({"\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2018": "'", "\u2019": "'"})


def depth_level_from_evidence(raw: object, sources: Sequence[str]) -> int | None:
    """원문에 없는 인용·잘못된 형식은 실패시킨다. 의미 분류 자체는 모델의 책임이다."""
    level, _ = depth_evidence_check(raw, sources)
    return level


def depth_evidence_check(raw: object, sources: Sequence[str]) -> tuple[int | None, str | None]:
    """깊이 등급과, 실패했다면 그 사유 코드(원문은 담지 않는다)."""
    if not isinstance(raw, dict) or set(raw) != set(_DESCRIPTIONS):
        return None, "evidence_format"
    normalized_sources = [_normalize(source) for source in sources]
    present: dict[str, bool] = {}
    for key in _DESCRIPTIONS:
        quote = raw[key]
        if not isinstance(quote, str):
            return None, "evidence_format"
        segments = _segments(quote)
        if len(segments) > 1 and any(len("".join(segment.split())) < MIN_JOINED_SEGMENT_CHARS for segment in segments):
            return None, f"evidence_fragment:{key}"
        if segments and not any(_in_order(segments, source) for source in normalized_sources):
            return None, f"evidence_mismatch:{key}"
        present[key] = bool(segments)

    if present["reason"]:
        return 2 + int(present["alternatives"]) + int(present["limitation"]), None
    return (1 if present["generic_reason"] else 0), None


def _normalize(text: str) -> str:
    return " ".join(text.translate(_QUOTE_MARKS).split())


def _in_order(segments: Sequence[str], source: str) -> bool:
    # 같은 답변 안에서 앞 조각이 끝난 뒤에 다음 조각이 있어야 한다. 순서를 바꿔 다른 인과를 만들지 못하게 한다.
    position = 0
    for segment in segments:
        found = source.find(segment, position)
        if found < 0:
            return False
        position = found + len(segment)
    return True


def _segments(quote: str) -> list[str]:
    # 문장 끝 기호로 나누고, 감싼 따옴표·괄호를 뗀다. 문장부호만 남은 조각은 비교하지 않는다.
    pieces = (piece.strip(_EDGE_CHARS) for piece in _SENTENCE_END.split(_normalize(quote)))
    return [piece for piece in pieces if piece]
