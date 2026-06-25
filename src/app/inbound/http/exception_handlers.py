
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.common.interview_qa.errors import PipelineError


def register_exception_handlers(app: FastAPI) -> None:

    @app.exception_handler(PipelineError)
    async def _handle_pipeline_error(_request: Request, exc: PipelineError) -> JSONResponse:
        # 도메인 메시지를 그대로 사용자에게 보여준다(한글).
        return JSONResponse(
            status_code=exc.status_code,
            content={"message": exc.message},
        )
