from __future__ import annotations

import inspect

# 1차: 용어 사전 없음. 2차에 "- Kafka: 카프카" 같은 줄을 담는다.
GLOSSARY_LINES: tuple[str, ...] = ()

_RULES = inspect.cleandoc(
    """
    [발음 문장(spoken_text) 작성]
    질문 본문(question)과 별도로, 같은 질문을 음성으로 읽을 때 쓸 발음 문장 spoken_text를 함께 제출한다.
    - spoken_text는 question과 의미, 어순, 존댓말, 문장 수가 같고, 영문, 숫자, 기호를 한국어로 읽는 표기로 바꾼 문장이다.
    - 변환 대상과 읽는 법은 다음과 같다.
      영문 단어: Redis는 레디스, Docker는 도커
      알파벳 약어: JWT는 제이더블유티, API는 에이피아이, SQL은 에스큐엘
      슬래시로 이은 표기: CI/CD는 씨아이 씨디, A/B는 에이 비
      기호가 붙은 이름: C++는 씨 플러스 플러스, C#은 씨 샵
      버전: v2는 브이 투, 3.0은 삼 점 영, React 18은 리액트 십팔
      숫자와 단위: 200ms는 이백 밀리초, 3개는 세 개, 6개월은 육 개월, 2.5%는 이 점 오 퍼센트, p95는 피 구십오
      코드 식별자: useState는 유즈스테이트, @Transactional은 트랜잭셔널
    - 읽는 법이 불확실한 용어는 한국어에서 가장 널리 쓰이는 발음으로 쓴다.
    - 바꾸지 마라: 질문의 의미, 어순, 존댓말, 문장 수, 수치의 값, 이미 한글로 쓰인 부분, 문장 부호.
    - 질문에 없는 설명이나 기술을 spoken_text에 추가하지 마라.
    - spoken_text에는 영문자, 괄호, 대괄호, 꺾쇠, 마크다운, 이모지를 넣지 마라.
    - question에 영문, 숫자, 기호가 하나도 없으면 spoken_text는 question과 똑같이 쓴다.
    """  # noqa: E501 -- Chat과 공유하는 규칙 문구를 그대로 유지한다.
)

_EXAMPLES = inspect.cleandoc(
    """
    예시
    question: Redis와 JWT를 사용한 이유를 설명해주세요.
    spoken_text: 레디스와 제이더블유티를 사용한 이유를 설명해주세요.
    question: p95 응답 시간이 200ms를 넘는 원인을 어떻게 분석하셨나요?
    spoken_text: 피 구십오 응답 시간이 이백 밀리초를 넘는 원인을 어떻게 분석하셨나요?
    question: A/B 테스트에서 전환율이 2.5% 상승했다면 이를 어떻게 검증하시겠어요?
    spoken_text: 에이 비 테스트에서 전환율이 이 점 오 퍼센트 상승했다면 이를 어떻게 검증하시겠어요?
    """
)


def _build_block() -> str:
    parts = [_RULES]
    if GLOSSARY_LINES:
        parts.append("\n".join(["[용어 읽기]", *GLOSSARY_LINES]))
    parts.append(_EXAMPLES)
    return "\n\n".join(parts)


SPOKEN_TEXT_PROMPT_BLOCK = _build_block()
