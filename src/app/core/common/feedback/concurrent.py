from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Coroutine
from typing import Any

from app.core.common.company.fit.dto import CompanyFitPayload


async def run_with_company_fit[T](
    grading: Awaitable[T],
    company_fit: Coroutine[Any, Any, CompanyFitPayload | None],
) -> tuple[T, CompanyFitPayload | None]:
    fit_task = asyncio.create_task(company_fit)
    try:
        result = await grading
        return result, await fit_task
    except BaseException:
        # 채점 실패와 요청 취소 모두 기업 호출을 취소하고 정리한 뒤 원래 예외를 전달한다.
        fit_task.cancel()
        await asyncio.gather(fit_task, return_exceptions=True)
        raise
