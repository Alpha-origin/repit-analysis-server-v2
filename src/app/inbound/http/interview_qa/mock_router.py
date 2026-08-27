
import asyncio
import logging
import uuid
from typing import Any

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, BackgroundTasks, status
from fastapi.responses import JSONResponse, Response

from app.core.common.interview_qa.dto import CallbackSuccess, InterviewQaResult
from app.core.common.interview_qa.ports.webhook_client import WebhookClient
from app.inbound.http.interview_qa.callbacks import interview_qa_mock_callback_router
from app.inbound.http.interview_qa.dto import GenerateRequest, JobAccepted
from app.inbound.http.interview_qa.mock_payload import MOCK_RESULT

logger = logging.getLogger(__name__)

# 실제 파이프라인이 수십 초 이상 걸리는 비동기 흐름을 흉내내기 위한 지연.
_CALLBACK_DELAY_SECONDS = 10


def make_interview_qa_mock_router() -> APIRouter:
    router = APIRouter(tags=["interview_qa_mock"])

    @router.post(
        "/generate-mock",
        status_code=status.HTTP_202_ACCEPTED,
        callbacks=interview_qa_mock_callback_router.routes,
    )
    @inject
    async def generate_mock(
        request: GenerateRequest,
        background_tasks: BackgroundTasks,
        webhook: FromDishka[WebhookClient],
    ) -> Response:

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
            content=accepted.model_dump(by_alias=True),
        )

    return router


async def _send_callback_after_delay(
    webhook: WebhookClient,
    callback_url: str,
    job_id: str,
) -> None:
    try:
        await asyncio.sleep(_CALLBACK_DELAY_SECONDS)
        # MOCK_RESULT 는 snake_case 원본이다. 실제 콜백과 표기가 갈리지 않도록
        # 반드시 CallbackSuccess 를 거쳐 내보낸다(스키마 검증도 덤으로 받는다).
        payload: dict[str, Any] = CallbackSuccess(
            job_id=job_id,
            result=InterviewQaResult.model_validate(MOCK_RESULT),
        ).model_dump(by_alias=True)
        await webhook.send(callback_url, payload)
        logger.info("interview_qa.generate_mock.callback_sent", extra={"job_id": job_id})
    except Exception:
        logger.exception("interview_qa.generate_mock.callback_failed", extra={"job_id": job_id})
