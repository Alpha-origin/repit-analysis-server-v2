import logging
import uuid

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, BackgroundTasks, status
from fastapi.responses import JSONResponse, Response

from app.core.commands.dispatch_feedback_multi import DispatchFeedbackMulti
from app.core.common.feedback.multi.dto import (
    FeedbackMultiRequest,
    FeedbackPersona,
    MultiFeedbackQuestion,
)
from app.core.common.feedback.solo.dto import FeedbackAnswer
from app.inbound.http.interview_feedback.multi.callbacks import feedback_multi_callback_router
from app.inbound.http.interview_feedback.multi.dto import (
    FeedbackMultiHttpRequest,
    MultiFeedbackJobAccepted,
)

logger = logging.getLogger(__name__)


def make_feedback_multi_router() -> APIRouter:
    router = APIRouter(tags=["feedback_multi"])

    @router.post(
        "/feedback/multi",
        status_code=status.HTTP_202_ACCEPTED,
        # 실제 결과는 이 응답이 아니라 콜백으로 간다. 그 페이로드를 OpenAPI 에 함께 싣는다.
        callbacks=feedback_multi_callback_router.routes,
    )
    @inject
    async def feedback_multi(
        request: FeedbackMultiHttpRequest,
        background_tasks: BackgroundTasks,
        dispatcher: FromDishka[DispatchFeedbackMulti],
    ) -> Response:
        job_id = str(uuid.uuid4())

        # HTTP DTO(HttpUrl) → 도메인 DTO(str) 로 변환.
        # Command 는 외부 표현(HttpUrl, camelCase alias) 을 모르고 평문만 다룬다.
        job_request = FeedbackMultiRequest(
            session_id=request.session_id,
            interview_id=request.interview_id,
            user_id=request.user_id,
            personas=tuple(
                FeedbackPersona(persona_id=persona.persona_id, role=persona.role, style=persona.style)
                for persona in request.personas
            ),
            questions=tuple(
                MultiFeedbackQuestion(
                    question_id=question.question_id,
                    persona_id=question.persona_id,
                    parent_id=question.parent_id,
                    type=question.type,
                    intention=question.intention,
                    content=question.content,
                    created_at=question.created_at,
                )
                for question in request.questions
            ),
            answers=tuple(
                FeedbackAnswer(
                    answer_id=answer.answer_id,
                    question_id=answer.question_id,
                    content=answer.content,
                    created_at=answer.created_at,
                )
                for answer in request.answers
            ),
            callback_url=str(request.callback_url),
        )

        # BackgroundTasks 에 등록하면 응답이 클라이언트에 전달된 직후 실행된다.
        # 작업은 fire-and-forget — 결과는 콜백으로만 알린다.
        background_tasks.add_task(dispatcher.execute, job_id, job_request)

        logger.info(
            "feedback_multi.accepted",
            extra={
                "job_id": job_id,
                "session_id": request.session_id,
                "persona_count": len(request.personas),
                "question_count": len(request.questions),
                "answer_count": len(request.answers),
            },
        )
        # 답변 본문 등 잠재적 민감 정보는 DEBUG 에만 풀어서 로깅.
        logger.debug(
            "feedback_multi.payload",
            extra={
                "job_id": job_id,
                "user_id": request.user_id,
                "interview_id": request.interview_id,
                "callback_url": str(request.callback_url),
            },
        )

        accepted = MultiFeedbackJobAccepted(job_id=job_id, session_id=request.session_id)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            # CamelModel 을 쓰는 응답은 by_alias=True 여야 camelCase 로 나간다.
            content=accepted.model_dump(by_alias=True),
        )

    return router
