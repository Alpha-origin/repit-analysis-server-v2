from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.common.interview_qa.errors import PipelineError

logger = logging.getLogger(__name__)

# 로그 한 줄이 통째로 요청 본문이 되지 않게 자르는 상한.
_MAX_INPUT_CHARS = 300
_MAX_BODY_CHARS = 4000


def register_exception_handlers(app: FastAPI) -> None:

    @app.exception_handler(PipelineError)
    async def _handle_pipeline_error(request: Request, exc: PipelineError) -> JSONResponse:
        # 어느 엔드포인트에서 어떤 도메인 규칙에 걸렸는지 남긴다.
        logger.warning(
            "pipeline_error %s %s status=%d message=%s",
            request.method,
            request.url.path,
            exc.status_code,
            exc.message,
        )
        # 도메인 메시지를 그대로 사용자에게 보여준다(한글).
        return JSONResponse(
            status_code=exc.status_code,
            content={"message": exc.message},
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_request_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        # FastAPI 기본 422 핸들러는 아무것도 로깅하지 않아서, 서버 쪽에서는
        # "왜 422 인지" 가 전혀 남지 않는다. 응답 형식은 기본 핸들러와 똑같이 두고
        # 로그만 추가한다(수신측 파싱을 깨지 않기 위함).
        errors = exc.errors()
        logger.warning(
            "request_validation_failed %s %s errors=%d %s\n%s",
            request.method,
            request.url.path,
            len(errors),
            _describe_body_shape(exc.body),
            _format_errors(errors),
        )
        # 질문 본문 등 잠재적 민감 정보가 섞이므로 실제 입력값은 DEBUG 에만 풀어둔다.
        # (APP_LOGGING_LEVEL=DEBUG 로 켠다)
        logger.debug(
            "request_validation_failed.detail %s %s\ninputs:\n%s\nbody: %s",
            request.method,
            request.url.path,
            _format_error_inputs(errors),
            _format_body(exc.body),
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": jsonable_encoder(errors)},
        )


def _format_errors(errors: Sequence[Any]) -> str:
    # 한 줄에 하나씩. loc 만 봐도 어느 필드가 문제인지 바로 보인다.
    #   - body.techPersona.questionCount | missing | Field required
    #   - body.questions[0].expectedAnswer | string_too_short | String should have ...
    return "\n".join(
        f"  - {_format_location(error.get('loc', ()))} | {error.get('type', '?')} | {error.get('msg', '')}"
        for error in errors
    )


def _format_error_inputs(errors: Sequence[Any]) -> str:
    return "\n".join(
        f"  - {_format_location(error.get('loc', ()))} = {_truncate(repr(error.get('input')), _MAX_INPUT_CHARS)}"
        for error in errors
    )


def _format_location(location: Sequence[Any]) -> str:
    # ("body", "questions", 0, "expectedAnswer") → body.questions[0].expectedAnswer
    rendered = ""
    for part in location:
        if isinstance(part, int):
            rendered += f"[{part}]"
        elif rendered:
            rendered += f".{part}"
        else:
            rendered = str(part)
    return rendered or "<root>"


def _describe_body_shape(body: Any) -> str:
    # 값은 빼고 최상위 키만 남긴다. camelCase/snake_case 오타나 필드 누락은 이것만 봐도 잡힌다.
    if isinstance(body, dict):
        return f"body_keys={sorted(body)}"
    if isinstance(body, list):
        return f"body=list(len={len(body)})"
    if body is None:
        return "body=<empty>"
    # JSON 파싱 자체가 깨진 경우 body 는 원문 문자열/바이트로 들어온다.
    return f"body=<unparsed {type(body).__name__}>"


def _format_body(body: Any) -> str:
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    if isinstance(body, str):
        return _truncate(body, _MAX_BODY_CHARS)
    try:
        dumped = json.dumps(jsonable_encoder(body), ensure_ascii=False)
    except (TypeError, ValueError):
        dumped = repr(body)
    return _truncate(dumped, _MAX_BODY_CHARS)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...(+{len(text) - limit} chars)"
