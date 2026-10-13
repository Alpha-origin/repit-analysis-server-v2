from __future__ import annotations


def clean_spoken_text(value: object, max_chars: int) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped or len(stripped) > max_chars:
        # 중간에서 자르면 질문과 의미가 달라지므로 발음 문장 전체를 버린다.
        return None
    return stripped
