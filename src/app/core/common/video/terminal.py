"""Build the one immutable terminal callback body of a video job."""

from __future__ import annotations

from app.core.common.video.dto import AnalysisOutcome, TerminalCallback, VideoRequest, VideoResult, public_error
from app.core.common.video.errors import PublicErrorCode
from app.core.common.video.ports import Stage, StageRecord

FILE_STAGES: tuple[Stage, ...] = ("source", "inspect", "validate")


def build_terminal_callback(job_id: str, request: VideoRequest, records: dict[Stage, StageRecord]) -> TerminalCallback:
    for stage in FILE_STAGES:
        record = records[stage]
        if record.status != "succeeded":
            # 파일·전처리 실패: 같은 공개 원인을 result.error 와 analysis.error 에 둔다.
            # 검증된 디코딩 타임라인이 없으므로 durationMs 는 null 이다.
            error = public_error(record.error_code or "INTERNAL_ERROR")
            result = VideoResult(
                video_id=request.video.video_id,
                duration_ms=None,
                status="unavailable",
                analysis=AnalysisOutcome(status="unavailable", data=None, error=error),
                error=error,
            )
            return _generated(job_id, request, result)
    validated = records["validate"].result or {}
    analyze = records["analyze"]
    if analyze.status == "succeeded" and analyze.result is not None:
        outcome = AnalysisOutcome.model_validate(analyze.result)
    else:
        # 파일은 유효했지만 분석 단계가 실패했다: result.error 는 null, analysis.error 만 채운다.
        outcome = AnalysisOutcome(status="unavailable", data=None, error=public_error(_cause(analyze)))
    result = VideoResult(
        video_id=request.video.video_id,
        duration_ms=int(validated["durationMs"]),
        status=outcome.status,
        analysis=outcome,
        error=None,
    )
    return _generated(job_id, request, result)


def failed_callback(job_id: str, request: VideoRequest, code: PublicErrorCode = "INTERNAL_ERROR") -> TerminalCallback:
    """Minimal body used only when the terminal document itself could not be generated."""
    return TerminalCallback(
        job_id=job_id,
        request_id=request.request_id,
        session_id=request.session_id,
        interview_id=request.interview_id,
        user_id=request.user_id,
        status="failed",
        result=None,
        error=public_error(code),
    )


def _generated(job_id: str, request: VideoRequest, result: VideoResult) -> TerminalCallback:
    return TerminalCallback(
        job_id=job_id,
        request_id=request.request_id,
        session_id=request.session_id,
        interview_id=request.interview_id,
        user_id=request.user_id,
        status=result.status,
        result=result,
        error=None,
    )


def _cause(record: StageRecord) -> PublicErrorCode:
    return record.error_code or "INTERNAL_ERROR"
