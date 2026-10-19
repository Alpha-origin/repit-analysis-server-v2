"""채점 응답 검증 실패를 구분하고 사유를 남긴다 (이슈 #46)."""

from __future__ import annotations

import logging

from app.core.common.interview_qa.errors import PipelineError

# 같은 입력으로 다시 부르는 최대 횟수. 응답을 섞지 않고 마지막 응답만 쓴다.
GRADING_ATTEMPTS = 2


class IncompleteGradingError(PipelineError):
    """응답은 왔지만 일부 문항·면접관이 검증을 못 넘었다. 같은 입력으로 한 번 더 부를 수 있다."""


def log_dropped(log: logging.Logger, event: str, key: object, reason: str, attempt: int) -> None:
    # 운영 로그 포맷은 extra 를 출력하지 않으므로 값을 메시지에도 싣는다. 인용·답변 원문은 남기지 않는다.
    log.warning(
        "%s key=%s reason=%s attempt=%d",
        event,
        key,
        reason,
        attempt,
        extra={"key": key, "reason": reason, "attempt": attempt},
    )
