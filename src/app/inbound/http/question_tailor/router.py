from __future__ import annotations

import logging
import uuid

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, BackgroundTasks, status
from fastapi.responses import JSONResponse

from app.core.commands.dispatch_question_tailor import DispatchQuestionTailor
from app.core.common.question_tailor.dto import (
    CandidateProfile,
    OriginalQuestion,
    QuestionTailorRequest,
)
from app.inbound.http.question_tailor.dto import TailorJobAccepted, TailorRequest

logger = logging.getLogger(__name__)


def make_question_tailor_router() -> APIRouter:
    router = APIRouter(tags=["question_tailor"])

    @router.post("/questions/tailor", status_code=status.HTTP_202_ACCEPTED)
    @inject
    async def tailor_questions(
        request: TailorRequest,
        background_tasks: BackgroundTasks,
        dispatcher: FromDishka[DispatchQuestionTailor],
    ) -> JSONResponse:
        job_id = str(uuid.uuid4())

        # HTTP DTO(HttpUrl) → 도메인 DTO(str) 로 변환.
        # Command 는 외부 표현(HttpUrl, camelCase alias) 을 모르고 평문만 다룬다.
        job_request = QuestionTailorRequest(
            interview_id=request.interview_id,
            user_id=request.user_id,
            profile=CandidateProfile(
                job_role=request.profile.job_role,
                experience_level=request.profile.experience_level,
                persona_type=request.profile.persona_type,
            ),
            questions=tuple(
                OriginalQuestion(
                    id=question.id,
                    category=question.category,
                    question=question.question,
                    expected_answer=question.expected_answer,
                    based_on=question.based_on,
                )
                for question in request.questions
            ),
            callback_url=str(request.callback_url),
        )

        # BackgroundTasks 에 등록하면 응답이 클라이언트에 전달된 직후 실행된다.
        # 작업은 fire-and-forget — 결과는 콜백으로만 알린다.
        background_tasks.add_task(dispatcher.execute, job_id, job_request)

        logger.info(
            "question_tailor.accepted",
            extra={
                "job_id": job_id,
                "interview_id": request.interview_id,
                "question_count": len(request.questions),
            },
        )
        # 질문 본문 등 잠재적 민감 정보는 DEBUG 에만 풀어서 로깅.
        logger.debug(
            "question_tailor.payload",
            extra={
                "job_id": job_id,
                "user_id": request.user_id,
                "callback_url": str(request.callback_url),
            },
        )

        accepted = TailorJobAccepted(job_id=job_id, interview_id=request.interview_id)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            # CamelModel 을 쓰는 응답은 by_alias=True 여야 camelCase 로 나간다.
            content=accepted.model_dump(by_alias=True),
        )

    return router
