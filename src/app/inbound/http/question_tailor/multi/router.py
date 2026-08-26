from __future__ import annotations

import logging
import uuid

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, BackgroundTasks, status
from fastapi.responses import JSONResponse

from app.core.commands.dispatch_question_tailor_multi import DispatchQuestionTailorMulti
from app.core.common.interview_qa.dto import CoreFeature, ProjectSummary, RepositorySummary
from app.core.common.question_tailor.dto import OriginalQuestion
from app.core.common.question_tailor.multi.dto import MultiTailorRequest, TailorPersona
from app.inbound.http.question_tailor.multi.dto import (
    MultiTailorHttpRequest,
    MultiTailorJobAccepted,
    ProjectSummaryRequest,
    TailorPersonaRequest,
)

logger = logging.getLogger(__name__)


def make_question_tailor_multi_router() -> APIRouter:
    router = APIRouter(tags=["question_tailor"])

    @router.post("/questions/tailor/multi", status_code=status.HTTP_202_ACCEPTED)
    @inject
    async def tailor_questions_multi(
        request: MultiTailorHttpRequest,
        background_tasks: BackgroundTasks,
        dispatcher: FromDishka[DispatchQuestionTailorMulti],
    ) -> JSONResponse:
        job_id = str(uuid.uuid4())

        # HTTP DTO(HttpUrl, camelCase alias) → 도메인 DTO(str) 로 변환.
        # Command 는 외부 표현을 모르고 평문만 다룬다.
        job_request = MultiTailorRequest(
            interview_id=request.interview_id,
            user_id=request.user_id,
            job_role=request.job_role,
            experience_level=request.experience_level,
            tech_persona=_to_persona(request.tech_persona),
            other_personas=tuple(_to_persona(persona) for persona in request.other_personas),
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
            project_summary=_to_project_summary(request.project_summary),
            callback_url=str(request.callback_url),
        )

        # BackgroundTasks 에 등록하면 응답이 클라이언트에 전달된 직후 실행된다.
        # 작업은 fire-and-forget — 결과는 콜백으로만 알린다.
        background_tasks.add_task(dispatcher.execute, job_id, job_request)

        logger.info(
            "question_tailor_multi.accepted",
            extra={
                "job_id": job_id,
                "interview_id": request.interview_id,
                "question_count": len(request.questions),
                "other_persona_count": len(request.other_personas),
            },
        )
        # 질문 본문 등 잠재적 민감 정보는 DEBUG 에만 풀어서 로깅.
        logger.debug(
            "question_tailor_multi.payload",
            extra={
                "job_id": job_id,
                "user_id": request.user_id,
                "callback_url": str(request.callback_url),
            },
        )

        accepted = MultiTailorJobAccepted(job_id=job_id, interview_id=request.interview_id)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            # CamelModel 을 쓰는 응답은 by_alias=True 여야 camelCase 로 나간다.
            content=accepted.model_dump(by_alias=True),
        )

    return router


def _to_persona(request: TailorPersonaRequest) -> TailorPersona:
    return TailorPersona(persona_id=request.persona_id, role=request.role, style=request.style)


def _to_project_summary(request: ProjectSummaryRequest) -> ProjectSummary:
    return ProjectSummary(
        overview=request.overview,
        repositories=[
            RepositorySummary(repo=repository.repo, role=repository.role, description=repository.description)
            for repository in request.repositories
        ],
        core_features=[
            CoreFeature(name=feature.name, description=feature.description, based_on=feature.based_on)
            for feature in request.core_features
        ],
        tech_stack=request.tech_stack,
    )
