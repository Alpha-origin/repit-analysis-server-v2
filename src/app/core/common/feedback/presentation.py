from __future__ import annotations

import inspect
import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

logger = logging.getLogger(__name__)

FOLLOW_UP_QUESTION_MAX = 200
FOLLOW_UP_QUESTION_SCHEMA: dict[str, Any] = {
    "type": "string",
    # strict 도구는 문자열 길이 제약을 지원하지 않는다. 길이는 서버에서 검증한다.
    "description": (
        "현재 질문과 실제 답변의 근거·예외·트레이드오프를 더 확인하는 예상 꼬리질문 한 문장. "
        f"1~{FOLLOW_UP_QUESTION_MAX}자 한국어로 쓴다."
    ),
}
FOLLOW_UP_QUESTION_GUIDANCE = inspect.cleandoc(
    """
    - follow_up_question: 실제 답변의 근거·예외·트레이드오프 중 하나를 더 확인하는 예상 꼬리질문 한 문장.
      현재 질문을 단순 반복하지 말고, 답변에 없는 경험·수치·구현을 했다고 전제하지 마라.
      질문만 200자 이내 한국어로 쓰고 점수·모범답안·목록 번호는 쓰지 마라.
      이 질문은 복습용이며 실제 면접에 삽입하거나 이미 출제된 질문이라고 주장하지 마라.
"""
)
_REFERENCE = re.compile(r"(?<![A-Za-z0-9_])(?P<prefix>Q|문항[ \t]*)(?P<id>[0-9]+)(?![A-Za-z0-9_])")


def normalize_follow_up_question(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if text and len(text) <= FOLLOW_UP_QUESTION_MAX:
            return text
    # 문장이나 잘못된 객체를 로그에 남기지 않는다. 선택 항목 실패는 채점 전체를 막지 않는다.
    logger.warning("feedback.follow_up_question.invalid")
    return None


def normalize_generated_fields(
    entry: Mapping[str, Any], fields: Sequence[str], question_ids: Sequence[str]
) -> dict[str, Any]:
    """확실한 내부 ID 참조만 표시 번호로 바꾼다. 원문과 식별자 필드는 호출자가 제외한다."""
    positions = {qid: str(index) for index, qid in enumerate(question_ids, 1)}
    ambiguous = {str(index) for index in range(1, len(question_ids) + 1)}

    def replace(match: re.Match[str]) -> str:
        qid = match["id"]
        # 표시 범위와 겹치는 값, 알 수 없는 값, 중복 ID는 의미를 추측하지 않는다.
        if qid in ambiguous or qid not in positions or question_ids.count(qid) != 1:
            return match[0]
        return match["prefix"] + positions[qid]

    def normalize(value: Any) -> Any:
        if isinstance(value, str):
            return _REFERENCE.sub(replace, value)
        if isinstance(value, list):
            return [_REFERENCE.sub(replace, item) if isinstance(item, str) else item for item in value]
        return value

    return {**entry, **{field: normalize(entry[field]) for field in fields if field in entry}}
