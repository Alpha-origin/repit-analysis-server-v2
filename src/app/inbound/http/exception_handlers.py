"""HTTP 예외 핸들러 등록 헬퍼.

도메인 예외(``PipelineError``) 를 ``{"message": "..."}`` 형태의 JSON 으로
변환해 응답한다. 상태 코드는 예외가 들고 있는 ``status_code`` 를 그대로 사용.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.common.interview_qa.errors import PipelineError


def register_exception_handlers(app: FastAPI) -> None:
    """FastAPI 앱에 도메인 예외 → HTTP 응답 매핑을 등록한다."""

    @app.exception_handler(PipelineError)
    async def _handle_pipeline_error(_request: Request, exc: PipelineError) -> JSONResponse:
        # 도메인 메시지를 그대로 사용자에게 보여준다(한글).
        return JSONResponse(
            status_code=exc.status_code,
            content={"message": exc.message},
        )
