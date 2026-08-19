from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

# 형태소 분석기 없이 한국어 단어 빈도를 세기 위한 최소 장치.
# 순수 공백 분리만 하면 "Redis를 / Redis로 / Redis는" 이 전부 다른 단어로 잡히므로
# 끝에 붙은 조사를 한 번 잘라낸다. 긴 조사부터 검사해야 "에서" 가 "에" 로 잘못 잘리지 않는다.
# 정확도가 부족해지면 이 모듈만 kiwipiepy 기반으로 교체하면 된다.
_PARTICLES: tuple[str, ...] = (
    "에서는",
    "에서도",
    "으로는",
    "으로도",
    "부터는",
    "까지는",
    "이라고",
    "에게서",
    "에서",
    "으로",
    "에게",
    "한테",
    "까지",
    "부터",
    "처럼",
    "보다",
    "마다",
    "조차",
    "밖에",
    "이나",
    "이란",
    "라고",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "의",
    "에",
    "로",
    "와",
    "과",
    "도",
    "만",
    "랑",
)

# 조사를 떼고 남은 어간이 이 길이 미만이면 자르지 않는다.
# ("평가" 에서 "가" 를 떼면 "평" 이 되는 식의 오작동 방지)
_MIN_STEM_LENGTH = 2

# 양 끝에서 제거할 문장부호. "C++" 의 +, "Node.js" 의 중간 . 은 살아남는다.
_PUNCTUATION = " \t\n\r.,!?()[]{}<>\"'`~:;/\\|·…“”‘’"  # noqa: RUF001

# 빈도 상위에 늘 올라오지만 의미가 없는 기능어·상투어.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "그리고",
        "하지만",
        "그래서",
        "그런데",
        "그래도",
        "때문",
        "때문에",
        "이런",
        "저런",
        "그런",
        "이것",
        "그것",
        "저것",
        "여기",
        "거기",
        "저기",
        "정도",
        "경우",
        "부분",
        "다음",
        "자체",
        "통해",
        "위해",
        "대해",
        "관련",
        "같이",
        "많이",
        "조금",
        "아주",
        "매우",
        "다시",
        "지금",
        "이제",
        "그냥",
        "어떤",
        "무슨",
        "저희",
        "제가",
        "우리",
        "그때",
        "당시",
        "다른",
        "모든",
        "있습니다",
        "합니다",
        "했습니다",
        "입니다",
        "됩니다",
        "습니다",
        "하고",
        "하는",
        "해서",
        "하여",
        "있는",
        "있고",
        "없는",
        "없고",
        "같은",
        "같습니다",
        "생각",
        "생각합니다",
    }
)


def extract_frequent_words(
    answers: Iterable[str],
    *,
    top_n: int = 10,
    min_count: int = 2,
) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    # 대소문자를 합쳐 세되(redis/Redis), 표시는 처음 등장한 형태를 쓴다.
    display: dict[str, str] = {}

    for answer in answers:
        for raw_token in answer.split():
            word = _normalize(raw_token)
            if word is None:
                continue
            key = word.lower()
            counter[key] += 1
            display.setdefault(key, word)

    return [(display[key], count) for key, count in counter.most_common(top_n) if count >= min_count]


def _normalize(raw_token: str) -> str | None:
    token = raw_token.strip(_PUNCTUATION)
    if not token:
        return None

    word = _strip_particle(token)
    if len(word) < _MIN_STEM_LENGTH or word in _STOPWORDS:
        return None
    # 문장부호만 남은 토큰 제거.
    if not any(character.isalnum() for character in word):
        return None
    return word


def _strip_particle(token: str) -> str:
    for particle in _PARTICLES:
        if not token.endswith(particle):
            continue
        stem = token[: -len(particle)]
        if len(stem) >= _MIN_STEM_LENGTH:
            return stem
        # 어간이 너무 짧으면 원래 단어가 통째로 그 형태인 것이므로 자르지 않는다.
        return token
    return token
