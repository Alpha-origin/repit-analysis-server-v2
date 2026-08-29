from __future__ import annotations

import logging

from app.main.logging_config import ContextFormatter


def test_context_formatter_renders_extra_fields_as_json() -> None:
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="feedback_solo.dispatch.stage.done",
        args=(),
        exc_info=None,
    )
    record.job_id = "job-1"
    record.duration_ms = 12.34

    rendered = ContextFormatter("%(message)s").format(record)

    assert rendered == ('feedback_solo.dispatch.stage.done {"duration_ms": 12.34, "job_id": "job-1"}')
