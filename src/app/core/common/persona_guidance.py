from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# REALISTIC/METICULOUS 는 기존 호출자의 키를 바꾼 새 표준 키다.
# 구버전 키는 분석 서버 선배포 기간 동안만 alias 로 받아 같은 지침으로 정규화한다.
PERSONA_TYPE_ALIASES: dict[str, str] = {
    "NEUTRAL": "REALISTIC",
    "STRESS": "METICULOUS",
}

PERSONA_TYPE_GUIDANCE: dict[str, str] = {
    "FRIENDLY": ("지원자를 존중하고 편안하게 대한다. 먼저 답변의 좋은 점을 인정한 뒤, 개선할 점을 부담 없이 짚는다."),
    "REALISTIC": (
        "실제 업무에서 마주칠 상황과 판단 근거를 중심으로 본다. 과장하지 않고 현실적인 기대와 한계를 분명히 한다."
    ),
    "METICULOUS": (
        "답변의 세부 사항과 근거를 꼼꼼히 확인한다. 모호한 표현은 구체화하도록 요구하되, 질문의 범위를 벗어나지 않는다."
    ),
}

PERSONA_TONE_GUIDANCE: dict[str, str] = {
    "GENTLE": "부드럽고 격려하는 표현을 사용한다. 부족한 점도 비난하지 말고 다음 개선 방향으로 안내한다.",
    "DIRECT": "간결하고 명확하게 말한다. 돌려 말하지 않되 평가 근거가 드러나도록 표현한다.",
    "PRESSURING": "긴장감 있고 직접적인 표현을 사용한다. 추가 확인을 요구할 수 있지만 모욕하거나 위협하지 않는다.",
}

_DEFAULT_PERSONA_TYPE_GUIDANCE = "특정 성향을 과장하지 말고, 질문의 목적과 답변의 내용에 집중해 균형 있게 판단한다."
_DEFAULT_PERSONA_TONE_GUIDANCE = "중립적이고 명확한 표현을 사용한다."


def build_persona_guidance(
    persona_type: str | None,
    persona_tone: str | None,
) -> list[str]:
    """성향과 어조를 별도 축으로 프롬프트에 넣는다.

    입력은 문자열로 열어 두어 새 키가 추가되어도 HTTP 422가 발생하지 않게 한다.
    알 수 없는 키는 경고를 남기고 해당 축의 기본 지침을 사용한다.
    """

    lines: list[str] = []

    type_key = _normalize_key(persona_type, PERSONA_TYPE_ALIASES, PERSONA_TYPE_GUIDANCE, "persona_type")
    if type_key is not None:
        label = "지침" if type_key in PERSONA_TYPE_GUIDANCE else "기본 지침"
        instruction = PERSONA_TYPE_GUIDANCE.get(type_key, _DEFAULT_PERSONA_TYPE_GUIDANCE)
        lines.append(f"성향({type_key}) {label}: {instruction}")

    tone_key = _normalize_key(persona_tone, {}, PERSONA_TONE_GUIDANCE, "persona_tone")
    if tone_key is not None:
        label = "지침" if tone_key in PERSONA_TONE_GUIDANCE else "기본 지침"
        instruction = PERSONA_TONE_GUIDANCE.get(tone_key, _DEFAULT_PERSONA_TONE_GUIDANCE)
        lines.append(f"어조({tone_key}) {label}: {instruction}")

    return lines


def _normalize_key(
    value: str | None,
    aliases: dict[str, str],
    guidance: dict[str, str],
    field_name: str,
) -> str | None:
    if value is None or not value.strip():
        return None

    raw_key = value.strip().upper()
    key = aliases.get(raw_key, raw_key)
    if key not in guidance:
        logger.warning(
            "persona_guidance.unknown_key",
            extra={"field": field_name, "value": value},
        )
    return key


# 질문 재작성(solo 테일러)과 신규 생성(N:1 테일러)이 함께 쓴다.
# 생성 단계에는 원질문이 없으므로 '원질문'을 전제로 한 문구를 쓰지 않는다.
QUESTION_PERSONA_TYPE_GUIDANCE: dict[str, str] = {
    "FRIENDLY": ("지원자가 편안하게 이해할 수 있도록 정중하고 부담 없는 표현을 사용한다."),
    "REALISTIC": (
        "실무적인 관점이 자연스럽게 드러나도록 표현한다. 주어지지 않은 업무 상황이나 경험을 사실처럼 전제하지 않는다."
    ),
    "METICULOUS": (
        "묻는 대상과 조건을 명확하게 표현한다. "
        "세부 사항을 확인한다는 이유로 한 문항에 여러 확인 항목을 덧붙이지 않는다."
    ),
}

QUESTION_PERSONA_TONE_GUIDANCE: dict[str, str] = {
    "GENTLE": "부드럽고 정중한 존댓말로 질문한다.",
    "DIRECT": "핵심을 바로 묻는 간결하고 명확한 표현을 사용한다.",
    "PRESSURING": (
        "간결하고 단호하게 질문하되, 모욕하거나 위협하지 않는다. "
        "압박감을 주려고 확인 항목을 늘리거나 난이도를 올리지 않는다."
    ),
}

_DEFAULT_QUESTION_PERSONA_TYPE_GUIDANCE = "질문의 검증 포인트를 유지하면서 자연스럽고 균형 있게 표현한다."
_DEFAULT_QUESTION_PERSONA_TONE_GUIDANCE = "중립적이고 명확한 존댓말로 질문한다."


def build_question_persona_guidance(
    persona_type: str | None,
    persona_tone: str | None,
) -> list[str]:
    """질문 재작성·생성 단계에서 사용할 성향과 어조 지침."""

    lines: list[str] = []

    type_key = _normalize_key(
        persona_type,
        PERSONA_TYPE_ALIASES,
        QUESTION_PERSONA_TYPE_GUIDANCE,
        "persona_type",
    )
    if type_key is not None:
        label = "지침" if type_key in QUESTION_PERSONA_TYPE_GUIDANCE else "기본 지침"
        instruction = QUESTION_PERSONA_TYPE_GUIDANCE.get(
            type_key,
            _DEFAULT_QUESTION_PERSONA_TYPE_GUIDANCE,
        )
        lines.append(f"성향({type_key}) {label}: {instruction}")

    tone_key = _normalize_key(
        persona_tone,
        {},
        QUESTION_PERSONA_TONE_GUIDANCE,
        "persona_tone",
    )
    if tone_key is not None:
        label = "지침" if tone_key in QUESTION_PERSONA_TONE_GUIDANCE else "기본 지침"
        instruction = QUESTION_PERSONA_TONE_GUIDANCE.get(
            tone_key,
            _DEFAULT_QUESTION_PERSONA_TONE_GUIDANCE,
        )
        lines.append(f"어조({tone_key}) {label}: {instruction}")

    return lines
