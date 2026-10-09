from __future__ import annotations

import logging
import uuid

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, BackgroundTasks, status
from fastapi.responses import JSONResponse

from app.core.commands.dispatch_applicant_profile import DispatchApplicantProfile
from app.core.common.applicant_profile.dto import ProfileJobRequest
from app.inbound.http.applicant_profile.dto import ProfileJobAccepted, ProfileRequest

logger = logging.getLogger(__name__)


def make_applicant_profile_router() -> APIRouter:
    router = APIRouter(tags=["applicant_profile"])

    @router.post("/profile", status_code=status.HTTP_202_ACCEPTED)
    @inject
    async def create_profile(
        request: ProfileRequest,
        background_tasks: BackgroundTasks,
        dispatcher: FromDishka[DispatchApplicantProfile],
    ) -> JSONResponse:
        job_id = str(uuid.uuid4())

        # HTTP DTO(HttpUrl) → 도메인 DTO(str) 로 변환.
        # Command 는 외부 표현(HttpUrl) 을 모르고, 평문 문자열만 다룬다.
        major = request.major.strip() if request.major else None
        job_request = ProfileJobRequest(
            major=major or None,
            portfolio_url=str(request.portfolio_url),
            github_urls=tuple(str(u) for u in request.github_urls),
            callback_url=str(request.callback_url),
        )

        # BackgroundTasks 에 등록하면 응답이 클라이언트에 전달된 직후 실행된다.
        # 작업은 fire-and-forget — 결과는 콜백으로만 알린다.
        background_tasks.add_task(dispatcher.execute, job_id, job_request)

        logger.info(
            "applicant_profile.accepted",
            extra={"job_id": job_id, "github_repo_count": len(request.github_urls)},
        )
        # portfolio_url 등 잠재적 민감 정보는 DEBUG 에만 풀어서 로깅.
        logger.debug(
            "applicant_profile.payload",
            extra={
                "job_id": job_id,
                "portfolio_url": str(request.portfolio_url),
                "github_urls": [str(u) for u in request.github_urls],
                "callback_url": str(request.callback_url),
            },
        )

        accepted = ProfileJobAccepted(job_id=job_id)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            # CamelModel 을 쓰는 응답은 by_alias=True 여야 camelCase 로 나간다.
            content=accepted.model_dump(by_alias=True),
        )

    return router
