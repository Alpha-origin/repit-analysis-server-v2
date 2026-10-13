import pytest

from app.core.common.spoken_text.sanitize import clean_spoken_text


@pytest.mark.parametrize("value", [None, 1, False, [], {}, "", " \n\t "])
def test_invalid_spoken_text_is_absent(value: object) -> None:
    assert clean_spoken_text(value, 600) is None


@pytest.mark.parametrize(
    ("value", "max_chars", "expected"),
    [
        (" \n레디스는 왜 사용했나요?\t ", 600, "레디스는 왜 사용했나요?"),
        ("가" * 600, 600, "가" * 600),
        ("가" * 601, 600, None),
        ("  질문  ", 2, "질문"),
        ("가" * 3, 2, None),
        ("JWT(제이더블유티) 설명해주세요.", 600, "JWT(제이더블유티) 설명해주세요."),
    ],
)
def test_trim_and_length_limit_without_truncation(value: str, max_chars: int, expected: str | None) -> None:
    # 영문·기호 및 의미 검증은 Chat 서버에서 처리한다.
    assert clean_spoken_text(value, max_chars) == expected
