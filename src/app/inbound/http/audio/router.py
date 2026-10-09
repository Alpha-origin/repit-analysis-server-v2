from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool

from app.core.common.audio.dto import AudioError, AudioPolicy, AudioRequest, IdempotencyConflictError, wire_payload
from app.core.common.audio.ports import AudioRepository
from app.core.common.audio.source_url import validate_source_url
from app.inbound.http.audio.dto import AudioAnalysisRequest


def validate_callback(url: str | None, allowed_hosts: list[str]) -> None:
    if url is None:
        return
    try:
        parsed = urlsplit(url)
        allowed = (
            parsed.scheme == "https"
            and parsed.hostname in allowed_hosts
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid callback URL") from exc
    if not allowed:
        raise HTTPException(status_code=422, detail="Callback must use HTTPS and an allowed host")


def make_audio_router(repository: AudioRepository, policy: AudioPolicy, callback_hosts: list[str]) -> APIRouter:
    router = APIRouter(tags=["audio"])

    @router.post("/analysis/audio", status_code=202)
    async def submit_remote(request: AudioAnalysisRequest) -> dict[str, str]:
        validate_callback(request.callback_url, callback_hosts)
        for recording in request.recordings:
            try:
                validate_source_url(recording.file_url, policy.source_hosts)
            except AudioError as exc:
                raise HTTPException(status_code=422, detail=exc.code) from exc
            if recording.file_size > policy.max_bytes:
                raise HTTPException(status_code=422, detail="source_too_large")
        accepted = await submit(request.to_job())
        return {**accepted, "requestId": request.request_id}

    @router.post("/audio/analysis", status_code=202, deprecated=True)
    async def submit(request: AudioRequest) -> dict[str, str]:
        validate_callback(request.callback_url, callback_hosts)
        try:
            job_id = await run_in_threadpool(repository.submit, request, policy)
        except IdempotencyConflictError as exc:
            raise HTTPException(
                status_code=409, detail="requestId already used with a different manifest or policy"
            ) from exc
        return {"jobId": job_id, "sessionId": request.session_id, "status": "accepted"}

    @router.get("/analysis/audio/jobs/{job_id}")
    @router.get("/audio/jobs/{job_id}", deprecated=True)
    async def status(job_id: str) -> dict[str, Any]:
        result = await run_in_threadpool(repository.snapshot, job_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Audio job not found")
        return dict(wire_payload(result))

    return router
