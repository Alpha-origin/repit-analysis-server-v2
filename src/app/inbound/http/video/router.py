from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import SecretStr, ValidationError
from starlette.concurrency import run_in_threadpool

from app.core.common.video.identity import request_fingerprint
from app.core.common.video.ports import (
    AdmissionError,
    Admit,
    ReplayConflictError,
    ReplayGoneError,
    RepositoryUnavailableError,
    SnapshotFound,
    SnapshotGone,
    VideoMetricsRepository,
    VideoRepository,
)
from app.inbound.http.video.auth import is_authenticated
from app.inbound.http.video.dto import VideoAnalysisRequest

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 64 * 1024


class ProblemError(Exception):
    def __init__(self, status: int, code: str, message: str, details: list[dict[str, Any]] | None = None) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message
        self.details = details

    def response(self) -> JSONResponse:
        content: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details is not None:
            content["details"] = self.details
        return JSONResponse(status_code=self.status, content=content)


def _unauthorized() -> ProblemError:
    return ProblemError(401, "UNAUTHORIZED", "X-Internal-Token 인증에 실패했습니다.")


def _too_large() -> ProblemError:
    return ProblemError(413, "PAYLOAD_TOO_LARGE", "요청 본문이 64KiB 를 넘었습니다.")


def _mapped(exc: Exception) -> ProblemError:
    if isinstance(exc, ReplayConflictError):
        return ProblemError(409, "REQUEST_ID_CONFLICT", "같은 requestId 로 다른 내용이 이미 접수되었습니다.")
    if isinstance(exc, ReplayGoneError):
        return ProblemError(410, "JOB_EXPIRED", "보관 기간이 끝난 요청입니다. 새 requestId 로 다시 요청해 주세요.")
    if isinstance(exc, AdmissionError):
        return ProblemError(
            422, "REQUEST_NOT_ADMITTED", "현재 정책으로 접수할 수 없는 요청입니다.", [{"reason": exc.reason}]
        )
    return ProblemError(503, "TEMPORARILY_UNAVAILABLE", "잠시 후 같은 요청으로 다시 시도해 주세요.")


async def _read_json(request: Request) -> Any:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise _too_large()
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            raise _too_large()
    try:
        return json.loads(body)
    except ValueError as exc:
        raise ProblemError(422, "INVALID_JSON", "요청 본문이 올바른 JSON 이 아닙니다.") from exc


def _parse(raw: Any) -> VideoAnalysisRequest:
    try:
        return VideoAnalysisRequest.model_validate(raw)
    except ValidationError as exc:
        # Only locations and error types: never echo inputs (signed URLs) back.
        details = [{"loc": list(error["loc"]), "type": error["type"]} for error in exc.errors(include_input=False)]
        raise ProblemError(422, "INVALID_REQUEST", "요청 형식이 올바르지 않습니다.", details) from exc


class _VideoEndpoints:
    def __init__(self, repository: VideoRepository, admit: Admit, api_token: SecretStr) -> None:
        self.repository = repository
        self.admit = admit
        self.api_token = api_token

    async def accept(self, request: Request) -> dict[str, str]:
        # Authenticate from headers BEFORE reading or parsing the body.
        if not is_authenticated(request, self.api_token):
            raise _unauthorized()
        video_request = _parse(await _read_json(request)).to_request()
        try:
            accepted = await run_in_threadpool(
                self.repository.submit, video_request, request_fingerprint(video_request), self.admit
            )
        except (ReplayConflictError, ReplayGoneError, AdmissionError, RepositoryUnavailableError) as exc:
            raise _mapped(exc) from exc
        logger.info("video.accepted job=%s created=%s", accepted.job_id, accepted.created)
        return {
            "jobId": accepted.job_id,
            "requestId": video_request.request_id,
            "sessionId": video_request.session_id,
            "status": "accepted",
        }

    async def find(self, job_id: str, request: Request) -> dict[str, Any]:
        # Authenticate before any lookup so job existence is never revealed to anonymous callers.
        if not is_authenticated(request, self.api_token):
            raise _unauthorized()
        try:
            outcome = await run_in_threadpool(self.repository.snapshot, job_id)
        except RepositoryUnavailableError as exc:
            raise _mapped(exc) from exc
        if isinstance(outcome, SnapshotFound):
            return outcome.snapshot.wire()
        if isinstance(outcome, SnapshotGone):
            raise ProblemError(410, "JOB_EXPIRED", "보관 기간이 끝난 작업입니다.")
        raise ProblemError(404, "JOB_NOT_FOUND", "영상 분석 작업을 찾을 수 없습니다.")


def make_video_router(repository: VideoRepository, admit: Admit, api_token: SecretStr) -> APIRouter:
    router = APIRouter(tags=["video"])
    endpoints = _VideoEndpoints(repository, admit, api_token)

    # Handlers take the raw Request (no typed body), so authentication always precedes parsing.
    @router.post("/analysis/video", status_code=202)
    async def submit(request: Request) -> JSONResponse:
        try:
            return JSONResponse(status_code=202, content=await endpoints.accept(request))
        except ProblemError as problem:
            return problem.response()

    @router.get("/analysis/video/jobs/{job_id}")
    async def lookup(job_id: str, request: Request) -> JSONResponse:
        try:
            return JSONResponse(status_code=200, content=await endpoints.find(job_id, request))
        except ProblemError as problem:
            return problem.response()

    return router


def make_video_metrics_router(repository: VideoMetricsRepository, api_token: SecretStr) -> APIRouter:
    router = APIRouter(tags=["internal"])

    @router.get("/internal/video/metrics", response_model=None)
    async def metrics(request: Request) -> PlainTextResponse | JSONResponse:
        if not is_authenticated(request, api_token):
            return _unauthorized().response()
        try:
            values = await run_in_threadpool(repository.operational_metrics)
        except (RepositoryUnavailableError, OSError):
            return _mapped(RepositoryUnavailableError()).response()
        body = "".join(f"# TYPE {name} gauge\n{name} {value}\n" for name, value in sorted(values.items()))
        return PlainTextResponse(body, media_type="text/plain; version=0.0.4")

    return router
