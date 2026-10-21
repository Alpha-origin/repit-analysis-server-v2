from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.core.common.feedback.competency.catalog import Competency
from app.core.common.feedback.quotes import quote_present
from app.core.common.feedback.scoring import _level

SLOTS = {
    Competency.KNOWLEDGE: {
        "mechanism": "동작 원리·인과 관계 설명",
        "condition": "적용 조건·경계·예외",
        "components": "동작 관계 없이 구성요소·상태를 나열한 부분",
        "term_only": "용어만 언급한 부분",
    },
    Competency.PROBLEM_SOLVING: {
        "reason": "특정 문제·제약·목표와 선택을 연결한 이유 또는 원인과 수정의 연결",
        "alternatives": "둘 이상의 대안을 비교한 차이·선택 기준",
        "limitation": "선택한 방식의 한계·적용 조건",
        "generic_reason": "좋아서 또는 많이 써서 같은 일반적인 이유",
    },
    Competency.OWNERSHIP: {
        "action": "본인이 실제로 한 행동 주어 생략도 인정",
        "method": "도구 이름만이 아닌 구체 방법·절차",
        "result": "실제 관찰된 결과 정성 변화 포함 측정 절차만은 제외",
        "team_only": "명시적으로 팀이 주어이고 본인 행동이 하나도 없는 부분",
        "stance": "실제 행동 없이 방침·의견만",
    },
    Competency.COLLABORATION: {
        "counterpart": "함께 일한 상대·이해관계자",
        "issue": "이견·조율이 필요했던 내용",
        "coordination": "본인이 한 조율·설득·합의 행동",
        "outcome": "합의·관계·일의 결과",
        "generic": "소통 일반론",
    },
    Competency.GROWTH: {
        "admission": "본인 쪽 실수·한계 인정",
        "lesson": "구체적으로 배운 점",
        "change": "이후 실제로 바꾼 행동·적용",
        "generic": "많이 배웠다 같은 일반론",
    },
    Competency.MOTIVATION: {
        "company_fact": "회사·서비스·직무에 대한 구체적인 사실",
        "personal_link": "그 사실을 본인의 기준·경험과 연결",
        "generic": "성장할 수 있는 회사라서 같은 일반론",
    },
}


def group_schema(comp: Competency) -> dict[str, Any]:
    props: dict[str, Any] = {
        slot: {"type": "string", "description": f"{description}의 가장 짧은 원문 인용 없으면 빈 문자열"}
        for slot, description in SLOTS[comp].items()
    }
    if comp == Competency.KNOWLEDGE:
        props["accuracy"] = {"anyOf": [{"type": "integer", "enum": list(range(5))}, {"type": "null"}]}
    return {"type": "object", "additionalProperties": False, "properties": props, "required": list(props)}


def check_group(comp: Competency, raw: object, sources: Sequence[str]) -> tuple[int | None, str | None]:
    keys = set(SLOTS[comp]) | ({"accuracy"} if comp == Competency.KNOWLEDGE else set())
    if not isinstance(raw, dict) or set(raw) != keys:
        return None, "evidence_format"
    for slot in SLOTS[comp]:
        quote = raw[slot]
        if not isinstance(quote, str):
            return None, "evidence_format"
        if quote.strip() and not quote_present(quote, sources):
            return None, f"evidence_mismatch:{comp.value.lower()}.{slot}"
    present = {slot for slot in SLOTS[comp] if raw[slot].strip() and quote_present(raw[slot], sources)}
    base = _base_level(comp, present)
    if comp == Competency.KNOWLEDGE:
        accuracy = raw["accuracy"]
        if accuracy is not None and _level(accuracy) is None:
            return None, "invalid_accuracy"
        base = min(base, accuracy if accuracy is not None else 4)
    return base, None


def _knowledge(slots: set[str]) -> int:
    if "mechanism" in slots:
        return 4 if "condition" in slots else 3
    return 2 if "components" in slots else int("term_only" in slots)


def _problem_solving(slots: set[str]) -> int:
    if "reason" in slots:
        return 2 + int("alternatives" in slots) + int("limitation" in slots)
    return int("generic_reason" in slots)


def _ownership(slots: set[str]) -> int:
    if "action" in slots:
        return 4 if {"method", "result"} <= slots else 3 if "method" in slots else 2
    return 2 if "team_only" in slots else int("stance" in slots)


def _collaboration(slots: set[str]) -> int:
    if "coordination" in slots:
        return 4 if {"counterpart", "issue", "outcome"} <= slots else 3 if {"counterpart", "issue"} <= slots else 2
    return 2 if {"counterpart", "issue"} <= slots else int("generic" in slots)


def _growth(slots: set[str]) -> int:
    if "lesson" in slots:
        return 4 if {"admission", "change"} <= slots else 3 if {"admission", "change"} & slots else 2
    return 2 if {"admission", "change"} & slots else int("generic" in slots)


def _motivation(slots: set[str]) -> int:
    if {"company_fact", "personal_link"} <= slots:
        return 4
    return 2 if {"company_fact", "personal_link"} & slots else int("generic" in slots)


def _base_level(comp: Competency, slots: set[str]) -> int:
    calculators = {
        Competency.KNOWLEDGE: _knowledge,
        Competency.PROBLEM_SOLVING: _problem_solving,
        Competency.OWNERSHIP: _ownership,
        Competency.COLLABORATION: _collaboration,
        Competency.GROWTH: _growth,
        Competency.MOTIVATION: _motivation,
    }
    return calculators[comp](slots)
