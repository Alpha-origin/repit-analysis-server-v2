"""답변 원문 인용 검증의 공통 규칙."""

from __future__ import annotations

import re
from collections.abc import Sequence

# 모델은 떨어진 두 문장을 한 인용으로 잇는 경우가 많다(이슈 #46). 문장 하나하나가 원문에 같은 순서로 있으면 인정한다.
# 마침표는 뒤에 공백·끝이 올 때만 문장 끝으로 본다. "3.5배"·"Node.js"를 쪼개면 지어낸 숫자가 통과한다.
_SENTENCE_END = re.compile(r"…+|\.{2,}|[.!?。](?=\s|$)")
_EDGE_CHARS = " \t\n\"'()[]{}<>「」『』,·"
# 곱은 따옴표(U+201C·201D·201E·2018·2019)를 곧은 따옴표로 맞춘다.
_QUOTE_MARKS = str.maketrans({"\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2018": "'", "\u2019": "'"})


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


def quote_present(quote: str, sources: Sequence[str]) -> bool:
    segments = _segments(quote)
    return bool(segments) and any(_in_order(segments, _normalize(source)) for source in sources)
