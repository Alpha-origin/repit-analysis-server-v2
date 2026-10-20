from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from typing import Any

# 프롬프트는 채점 대상 문항을 [Q1], [Q2] … 블록 번호로 준다(웹 상세 화면의 Q 번호와 같다).
# 영문·숫자에 붙은 Q(예: "SQ1", "Q12a")는 문항 번호로 보지 않는다.
_QUESTION_LABEL = re.compile(r"(?<![A-Za-z0-9_])Q(\d+)(?![A-Za-z0-9_])")

# 모델이 쓴 문장 필드. id·인용(depth_evidence)은 문장이 아니므로 보지 않는다.
OVERALL_TEXT_FIELDS = ("summary", "strengths", "improvements")
FEEDBACK_TEXT_FIELDS = ("model_answer", "strengths", "improvements", "comment")
PERSONA_TEXT_FIELDS = ("comment", "strengths", "improvements")


def field_texts(entries: Iterable[Mapping[str, Any]], fields: Iterable[str]) -> list[object]:
    keys = tuple(fields)
    return [entry.get(key) for entry in entries for key in keys]


def out_of_range_labels(texts: Iterable[object], target_count: int) -> list[str]:
    """채점 대상 수보다 큰 Q 번호를 찾는다. 모델이 question_id 값을 번호로 쓴 흔적이다."""
    found: list[str] = []
    for text in _strings(texts):
        for match in _QUESTION_LABEL.finditer(text):
            label = match.group(0)
            if int(match.group(1)) > target_count and label not in found:
                found.append(label)
    return found


def warn_out_of_range_labels(
    log: logging.Logger | logging.LoggerAdapter[logging.Logger],
    event: str,
    texts: Iterable[object],
    target_count: int,
) -> None:
    # 경고 로그만 남긴다. id와 번호 범위가 겹치면 치환이 오히려 틀리므로 재시도·자동 치환은 하지 않는다.
    labels = out_of_range_labels(texts, target_count)
    if labels:
        # 운영 로그 포맷은 extra 를 출력하지 않으므로 값을 메시지에도 싣는다.
        log.warning(
            "%s labels=%s target_count=%d",
            event,
            ",".join(labels),
            target_count,
            extra={"labels": labels, "target_count": target_count},
        )


def _strings(values: Iterable[object]) -> Iterable[str]:
    for value in values:
        if isinstance(value, str):
            yield value
        elif isinstance(value, list):
            yield from (item for item in value if isinstance(item, str))
