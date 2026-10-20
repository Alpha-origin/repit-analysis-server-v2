from __future__ import annotations

import logging
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_job_id: ContextVar[str | None] = ContextVar("company_fit_job_id", default=None)


@contextmanager
def company_fit_job(job_id: str) -> Iterator[None]:
    token = _job_id.set(job_id)
    try:
        yield
    finally:
        _job_id.reset(token)


class CompanyFitLogger(logging.LoggerAdapter[logging.Logger]):
    def process(self, msg: Any, kwargs: MutableMapping[str, Any]) -> tuple[Any, MutableMapping[str, Any]]:
        job_id = _job_id.get()
        if job_id is not None:
            # job_id는 HTTP 진입점에서 발급한 UUID다. 모델 응답·사용자 입력은 넣지 않는다.
            msg = f"{msg} job_id={job_id}"
            kwargs["extra"] = {**kwargs.get("extra", {}), "job_id": job_id}
        return msg, kwargs
