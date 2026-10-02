from __future__ import annotations

import io
import logging

import pytest

from app.core.common.security.redaction import redact_text, redact_url
from app.main.log_redaction import RedactingFilter

SIGNED = (
    "https://bucket.s3.ap-northeast-2.amazonaws.com/v.webm?X-Amz-Credential=AKID&X-Amz-Signature=deadbeef"
    "&X-Amz-Security-Token=sessiontoken"
)


def test_redact_url_and_text() -> None:
    assert redact_url(SIGNED) == "https://bucket.s3.ap-northeast-2.amazonaws.com/v.webm?[redacted]"
    assert redact_url("https://user:pw@api.example.com:8443/cb") == "https://api.example.com/cb"
    text = redact_text(f"failed GET {SIGNED} token=abc X-Internal-Token: xyz")
    for secret in ("deadbeef", "AKID", "sessiontoken", "token=abc", "xyz"):
        assert secret not in text


def _render(logger_name: str, emit: object) -> str:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s %(url)s"))
    handler.addFilter(RedactingFilter())
    logger = logging.getLogger(logger_name)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        assert callable(emit)
        emit(logger)
    finally:
        logger.removeHandler(handler)
    return stream.getvalue()


def test_all_callback_logs_are_safe() -> None:
    output = _render(
        "tests.redaction.callbacks",
        lambda logger: logger.info("callback to %s", SIGNED, extra={"url": "https://u:p@api.example.com/cb?t=1"}),
    )
    for secret in ("deadbeef", "sessiontoken", "u:p"):
        assert secret not in output
    assert "bucket.s3.ap-northeast-2.amazonaws.com/v.webm" in output  # still useful for operators


def test_exception_body_and_signed_url_do_not_leak() -> None:
    def emit(logger: logging.Logger) -> None:
        try:
            raise RuntimeError(f"upstream said: {SIGNED}")
        except RuntimeError:
            logger.exception("failed", extra={"url": "-"})

    output = _render("tests.redaction.exceptions", emit)
    assert "deadbeef" not in output
    assert "RuntimeError" in output  # traceback is kept, only secrets are scrubbed


@pytest.mark.parametrize(
    "line",
    [
        '127.0.0.1:5000 - "GET /analysis/video/jobs/abc?X-Amz-Signature=deadbeef HTTP/1.1" 200',
        '127.0.0.1:5000 - "POST /callback?token=deadbeef HTTP/1.1" 200',
    ],
)
def test_uvicorn_access_relative_query_redacted(line: str) -> None:
    output = _render("uvicorn.access", lambda logger: logger.info("%s", line, extra={"url": "-"}))
    assert "deadbeef" not in output
    assert "?[redacted]" in output
