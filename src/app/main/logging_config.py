from __future__ import annotations

import json
import logging

_STANDARD_LOG_RECORD_FIELDS = frozenset(vars(logging.makeLogRecord({}))) | {"asctime", "message"}


class ContextFormatter(logging.Formatter):
    """기본 로그 뒤에 ``extra`` 로 전달된 구조화 문맥을 JSON 으로 붙인다."""

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        context = {key: value for key, value in record.__dict__.items() if key not in _STANDARD_LOG_RECORD_FIELDS}
        if not context:
            return rendered
        return f"{rendered} {json.dumps(context, ensure_ascii=False, default=str, sort_keys=True)}"


def setup_logging(level: str) -> None:
    formatter = ContextFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    root_logger = logging.getLogger()
    root_logger.setLevel(level.upper())

    if not root_logger.handlers:
        root_logger.addHandler(logging.StreamHandler())

    # uvicorn 이 먼저 핸들러를 구성한 경우에도 애플리케이션의 구조화 문맥이 출력되게 한다.
    for handler in root_logger.handlers:
        handler.setFormatter(formatter)
