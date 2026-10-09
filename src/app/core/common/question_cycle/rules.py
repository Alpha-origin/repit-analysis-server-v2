from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.core.common.applicant_profile.evidence import CATEGORIES, normalize_path
from app.core.common.interview_qa.dto import QuestionCategory
from app.core.common.question_cycle.dto import CycleMode, CycleQuestion

# 한 사이클의 세트 수. 면접 한 번에 세트 하나를 꺼내 쓰므로 사이클 하나가 면접 3회 분량이다.
SET_COUNT = 3

# 질문 본문 상한. 프롬프트에는 200자로 못박지만 모델이 넘길 수 있어 서버에서 한 번 더 자른다.
# multi/generate.py 와 같은 이유 — 소켓 서버의 질문 칸이 VARCHAR(255) 다.
_QUESTION_MAX_CHARS = 250


@dataclass(frozen=True)
class ModeRule:
    per_set: int  # 세트당 문항 수
    # True 면 세트마다 이용 가능한 카테고리를 하나씩 모두 담는다(SOLO).
    # False 면 세트 안 카테고리만 서로 다르고, 사이클 전체에서 모두 담는다(MULTI).
    every_category_per_set: bool

    @property
    def total(self) -> int:
        return self.per_set * SET_COUNT


MODE_RULES: dict[CycleMode, ModeRule] = {
    "SOLO": ModeRule(per_set=5, every_category_per_set=True),
    "MULTI": ModeRule(per_set=2, every_category_per_set=False),
}


def parse_questions(
    raw: dict[str, Any] | None,
    expected_answer_max_chars: int,
) -> tuple[list[CycleQuestion], list[str]]:
    """도구 입력을 질문 목록으로 바꾼다. 형식이 깨진 항목은 버리고 위반 내용으로 돌려준다."""
    if raw is None:
        return [], ["submit_question_cycle 도구로 제출하지 않았다."]
    entries = raw.get("questions")
    if not isinstance(entries, list):
        return [], ["questions 배열이 없다."]

    questions: list[CycleQuestion] = []
    violations: list[str] = []
    for number, entry in enumerate(entries, start=1):
        parsed = _parse_entry(entry, expected_answer_max_chars)
        if isinstance(parsed, str):
            violations.append(f"{number}번째 질문: {parsed}")
        else:
            questions.append(parsed)
    return questions, violations


def find_violations(
    questions: Sequence[CycleQuestion],
    mode: CycleMode,
    available: Sequence[QuestionCategory],
    evidence: frozenset[str],
) -> list[str]:
    """사이클 구성 규칙 위반을 사람이 읽을 문장으로 돌려준다. 비어 있으면 통과."""
    rule = MODE_RULES[mode]
    violations: list[str] = []

    if len(questions) != rule.total:
        violations.append(f"질문은 정확히 {rule.total}개여야 하는데 {len(questions)}개다.")

    unavailable = sorted({q.category for q in questions if q.category not in available})
    if unavailable:
        violations.append(
            f"재료가 없는 카테고리를 썼다: {', '.join(unavailable)}. 쓸 수 있는 것: {', '.join(available)}"
        )

    violations.extend(_set_violations(questions, rule, available))

    for q in questions:
        unknown = [path for path in q.based_on if path not in evidence]
        if unknown:
            violations.append(
                f"세트 {q.set_no}의 {q.category} 질문 basedOn 에 목록에 없는 경로가 있다: {', '.join(unknown)}"
            )
    return violations


def sort_questions(questions: Sequence[CycleQuestion]) -> list[CycleQuestion]:
    # 같은 세트 안에서 카테고리가 겹칠 수 있어(재료가 부족한 경우) 안정 정렬로 제출 순서를 남긴다.
    return sorted(questions, key=lambda q: (q.set_no, CATEGORIES.index(q.category)))


def _set_violations(
    questions: Sequence[CycleQuestion],
    rule: ModeRule,
    available: Sequence[QuestionCategory],
) -> list[str]:
    violations: list[str] = []
    out_of_range = sorted({q.set_no for q in questions if q.set_no > SET_COUNT})
    if out_of_range:
        violations.append(f"setNo 는 1~{SET_COUNT} 이어야 하는데 {out_of_range} 를 썼다.")

    for set_no in range(1, SET_COUNT + 1):
        in_set = [q.category for q in questions if q.set_no == set_no]
        if len(in_set) != rule.per_set:
            violations.append(f"세트 {set_no}에는 질문이 {rule.per_set}개여야 하는데 {len(in_set)}개다.")
        if rule.every_category_per_set:
            missing = [category for category in available if category not in in_set]
            if missing:
                violations.append(f"세트 {set_no}에 빠진 카테고리: {', '.join(missing)}")
        else:
            duplicated = sorted(category for category, count in Counter(in_set).items() if count > 1)
            if duplicated:
                violations.append(f"세트 {set_no} 안에서 카테고리가 겹친다: {', '.join(duplicated)}")

    if not rule.every_category_per_set:
        used = {q.category for q in questions}
        missing = [category for category in available if category not in used]
        if missing:
            violations.append(f"사이클 전체에 빠진 카테고리: {', '.join(missing)}")
    return violations


def _parse_entry(entry: object, expected_answer_max_chars: int) -> CycleQuestion | str:
    if not isinstance(entry, dict):
        return "객체가 아니다."

    set_no = entry.get("set_no")
    if not isinstance(set_no, int) or isinstance(set_no, bool) or set_no < 1:
        return "set_no 가 1 이상의 정수가 아니다."

    category = next((known for known in CATEGORIES if known == entry.get("category")), None)
    if category is None:
        return f"category 가 {', '.join(CATEGORIES)} 중 하나가 아니다."

    question = _clean_text(entry.get("question"))
    intention = _clean_text(entry.get("intention"))
    expected_answer = _clean_text(entry.get("expected_answer"))
    if question is None or intention is None or expected_answer is None:
        return "question, intention, expected_answer 는 비어 있으면 안 된다."

    based_on = _clean_based_on(entry.get("based_on"))
    if not based_on:
        return "based_on 이 비어 있다. 근거 경로를 1개 이상 넣어야 한다."

    return CycleQuestion(
        set_no=set_no,
        category=category,
        question=question[:_QUESTION_MAX_CHARS],
        intention=intention,
        # 길이를 넘겼다고 사이클 전체를 다시 만들 일은 아니라서 위반으로 보지 않고 자른다.
        expected_answer=expected_answer[:expected_answer_max_chars],
        based_on=based_on,
    )


def _clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _clean_based_on(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    paths = (normalize_path(item) for item in value if isinstance(item, str))
    return list(dict.fromkeys(path for path in paths if path))
