"""Last-line defence: strip signed URL queries, userinfo and token-like pairs from every log record."""

from __future__ import annotations

import logging

from app.core.common.security.redaction import redact_text

_STANDARD = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}
# Loggers that configure their own handlers or are emitted before ours exist.
_EXTRA_LOGGERS = ("uvicorn", "uvicorn.access", "uvicorn.error", "httpx", "httpcore")


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except (TypeError, ValueError):
            message = str(record.msg)
        record.msg = redact_text(message)
        record.args = None
        for key, value in list(record.__dict__.items()):
            if key not in _STANDARD and isinstance(value, str):
                setattr(record, key, redact_text(value))
        if record.exc_info and record.exc_info[1] is not None:
            # Keep the traceback for debugging but scrub URLs/tokens embedded in exception messages.
            record.exc_text = redact_text(_FORMATTER.formatException(record.exc_info))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact_text(record.exc_text)
        return True


_FORMATTER = logging.Formatter()


_FILTER = RedactingFilter()


def install_log_redaction() -> None:
    root = logging.getLogger()
    for handler in root.handlers:
        if _FILTER not in handler.filters:
            handler.addFilter(_FILTER)
    for name in _EXTRA_LOGGERS:
        logger = logging.getLogger(name)
        if _FILTER not in logger.filters:
            logger.addFilter(_FILTER)
        for handler in logger.handlers:
            if _FILTER not in handler.filters:
                handler.addFilter(_FILTER)
    # httpx logs full request URLs (including presigned query strings) at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
