from __future__ import annotations

import logging
import uuid

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, BackgroundTasks, status
from fastapi.responses import JSONResponse

from app.core.commands.dispatch_question_cycle import DispatchQuestionCycle
from app.core.common.question_cycle.dto import QuestionCycleRequest
from app.inbound.http.question_cycle.dto import QuestionCycleHttpRequest, QuestionCycleJobAccepted

logger = logging.getLogger(__name__)


def make_question_cycle_router() -> APIRouter:
    router = APIRouter(tags=["question_cycle"])

    @router.post("/questions/cycle", status_code=status.HTTP_202_ACCEPTED)
    @inject
    async def create_question_cycle(
        request: QuestionCycleHttpRequest,
        background_tasks: BackgroundTasks,
        dispatcher: FromDishka[DispatchQuestionCycle],
    ) -> JSONResponse:
        job_id = str(uuid.uuid4())

        # HTTP DTO(HttpUrl) → 도메인 DTO(str) 로 변환.
        # 빈 문자열 제외 질문은 프롬프트에 빈 줄만 남기므로 여기서 거른다.
        job_request = QuestionCycleRequest(
            mode=request.mode,
            profile=request.profile,
            exclude_questions=tuple(q.strip() for q in request.exclude_questions if q.strip()),
            callback_url=str(request.callback_url),
        )

        # BackgroundTasks 에 등록하면 응답이 클라이언트에 전달된 직후 실행된다.
        # 작업은 fire-and-forget — 결과는 콜백으로만 알린다.
        background_tasks.add_task(dispatcher.execute, job_id, job_request)

        logger.info(
            "question_cycle.accepted",
            extra={"job_id": job_id, "mode": request.mode, "exclude_count": len(request.exclude_questions)},
        )
        logger.debug(
            "question_cycle.payload",
            extra={"job_id": job_id, "callback_url": str(request.callback_url)},
        )

        accepted = QuestionCycleJobAccepted(job_id=job_id)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            # CamelModel 을 쓰는 응답은 by_alias=True 여야 camelCase 로 나간다.
            content=accepted.model_dump(by_alias=True),
        )

    return router
