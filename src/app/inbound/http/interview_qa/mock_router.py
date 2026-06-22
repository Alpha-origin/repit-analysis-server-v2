"""면접 Q&A 모킹 라우터.

POST /generate-mock — 실제 파이프라인을 돌리지 않고, 지연 후 정적 결과 페이로드를
``callback_url`` 로 POST 한다. 콜백 수신측을 빠르게 테스트하기 위한 용도.
즉시 응답 형식(202 + job_id)·콜백 페이로드 스키마는 ``/generate`` 와 동일하다.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, BackgroundTasks, status
from fastapi.responses import JSONResponse

from app.core.common.interview_qa.ports.webhook_client import WebhookClient
from app.inbound.http.interview_qa.dto import GenerateRequest, JobAccepted
from app.inbound.http.interview_qa.mock_payload import MOCK_RESULT

logger = logging.getLogger(__name__)

# 실제 파이프라인이 수십 초 이상 걸리는 비동기 흐름을 흉내내기 위한 지연.
_CALLBACK_DELAY_SECONDS = 30


def make_interview_qa_mock_router() -> APIRouter:
    """면접 Q&A 모킹 라우터 팩토리.

    실제 라우터(``make_interview_qa_router``) 와 같은 입력/즉시 응답 형식을 유지하되,
    파이프라인 대신 ``_CALLBACK_DELAY_SECONDS`` 만큼 대기 후 정적 페이로드를 콜백으로 보낸다.
    """
    router = APIRouter(tags=["interview_qa_mock"])

    @router.post("/generate-mock", status_code=status.HTTP_202_ACCEPTED)
    @inject
    async def generate_mock(
        request: GenerateRequest,
        background_tasks: BackgroundTasks,
        webhook: FromDishka[WebhookClient],
    ) -> JSONResponse:
        """모킹 작업 접수 — 지연 후 정적 결과를 ``callback_url`` 로 POST 한다."""
        job_id = str(uuid.uuid4())

        background_tasks.add_task(
            _send_callback_after_delay,
            webhook,
            str(request.callback_url),
            job_id,
        )

        logger.info(
            "interview_qa.generate_mock.accepted",
            extra={"job_id": job_id, "github_repo_count": len(request.github_urls)},
        )

        accepted = JobAccepted(job_id=job_id)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content=accepted.model_dump(),
        )

    return router


async def _send_callback_after_delay(
    webhook: WebhookClient,
    callback_url: str,
    job_id: str,
) -> None:
    """지연 후 정적 페이로드를 콜백으로 전송. 백그라운드에서 호출되므로 예외는 삼킨다."""
    try:
        await asyncio.sleep(_CALLBACK_DELAY_SECONDS)
        payload: dict[str, Any] = {
            "job_id": job_id,
            "status": "succeeded",
            "result": MOCK_RESULT,
        }
        await webhook.send(callback_url, payload)
        logger.info("interview_qa.generate_mock.callback_sent", extra={"job_id": job_id})
    except Exception:
        logger.exception("interview_qa.generate_mock.callback_failed", extra={"job_id": job_id})
