from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator
from pydantic.alias_generators import to_camel

from app.core.common.video.errors import MESSAGES, RETRYABLE, PublicErrorCode

SCHEMA_VERSION = "1"

QualityStatus = Literal["ready", "partial", "unavailable"]
CallbackStatus = Literal["ready", "partial", "unavailable", "failed"]
JobStatus = Literal["processing", "completed", "failed"]
DeliveryStatus = Literal["pending", "sending", "delivered", "failed"]


class WireModel(BaseModel):
    """camelCase on the wire, snake_case in Python. Always dump with ``wire()``."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
    )

    def wire(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json")


# ---------------------------------------------------------------- accepted request (internal)


class VideoSource(WireModel):
    video_id: str
    file_url: str  # 원문 그대로. 비교용 정규형은 identity 모듈이 따로 만든다.
    content_type: str
    file_size: int = Field(gt=0)
    uploaded_at: str


class VideoRequest(WireModel):
    request_id: str
    session_id: str
    interview_id: str
    user_id: str
    callback_url: str
    video: VideoSource


# ---------------------------------------------------------------- public errors and terminal callback


class PublicError(WireModel):
    code: PublicErrorCode
    message: str
    retryable: bool

    @model_validator(mode="after")
    def retryable_matches_table(self) -> PublicError:
        if self.retryable != RETRYABLE[self.code]:
            raise ValueError("retryable must match the public error table")
        return self


def public_error(code: PublicErrorCode) -> PublicError:
    return PublicError(code=code, message=MESSAGES[code], retryable=RETRYABLE[code])


def _ensure_finite_json(value: JsonValue) -> JsonValue:
    # NaN/Infinity 는 JSON 이 아니다. 저장·전송 본문에 섞이면 수신측 파싱이 깨진다.
    json.dumps(value, allow_nan=False)
    return value


class AnalysisOutcome(WireModel):
    """What a (future) analyzer returns. ``data`` is an opaque JSON value; no behavior schema is defined here."""

    status: QualityStatus
    data: JsonValue = None
    error: PublicError | None = None

    @field_validator("data")
    @classmethod
    def finite(cls, value: JsonValue) -> JsonValue:
        return _ensure_finite_json(value)

    @model_validator(mode="after")
    def consistent(self) -> AnalysisOutcome:
        if self.status == "unavailable":
            if self.data is not None or self.error is None:
                raise ValueError("unavailable analysis needs data=null and an error")
        elif self.data is None:
            raise ValueError("ready/partial analysis needs data")
        if self.status == "ready" and self.error is not None:
            raise ValueError("ready analysis cannot carry an error")
        return self


class VideoResult(WireModel):
    video_id: str
    duration_ms: int | None = Field(default=None, ge=0)
    status: QualityStatus
    analysis: AnalysisOutcome
    error: PublicError | None = None

    @model_validator(mode="after")
    def consistent(self) -> VideoResult:
        if self.status != self.analysis.status:
            raise ValueError("result.status must equal analysis.status")
        if self.error is not None and (self.status != "unavailable" or self.analysis.error != self.error):
            raise ValueError("a file-level error makes the result unavailable with the same analysis error")
        return self


class TerminalCallback(WireModel):
    schema_version: Literal["1"] = "1"
    job_id: str
    request_id: str
    session_id: str
    interview_id: str
    user_id: str
    status: CallbackStatus
    result: VideoResult | None
    error: PublicError | None

    @model_validator(mode="after")
    def consistent(self) -> TerminalCallback:
        if self.status == "failed":
            if self.result is not None or self.error is None:
                raise ValueError("failed callback needs result=null and a top-level error")
        elif self.result is None or self.error is not None or self.result.status != self.status:
            raise ValueError("generated callback needs a result with the same status and error=null")
        return self


class JobSnapshot(WireModel):
    """GET wrapper. ``result`` is the entire stored terminal callback, failed callbacks included."""

    job_id: str
    request_id: str
    session_id: str
    status: JobStatus
    created_at: str
    finished_at: str | None
    callback_status: DeliveryStatus
    result: TerminalCallback | None
    error: PublicError | None

    @model_validator(mode="after")
    def consistent(self) -> JobSnapshot:
        if self.status == "processing":
            if self.finished_at is not None or self.result is not None or self.error is not None:
                raise ValueError("processing snapshot has no finish, result or error")
            return self
        if self.finished_at is None or self.result is None:
            raise ValueError("terminal snapshot needs finishedAt and the stored callback")
        if self.status == "failed":
            if self.result.status != "failed" or self.error is None or self.error != self.result.error:
                raise ValueError("failed snapshot error must equal the failed callback error")
        elif self.result.status == "failed" or self.error is not None:
            raise ValueError("completed snapshot has a generated callback and no job error")
        return self


# ---------------------------------------------------------------- analyzer input


class PreparedVideo(BaseModel):
    """A source file that passed every real-media check. Future analyzers receive only this."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    video_id: str
    path: str
    sha256: str
    bytes: int = Field(gt=0)
    container: Literal["webm", "mp4"]
    codec: Literal["vp8", "vp9", "h264"]
    display_long_edge: int = Field(gt=0)
    display_short_edge: int = Field(gt=0)
    rotation_degrees: float
    frame_count: int = Field(gt=0)
    duration_ms: int = Field(ge=0)
    average_fps: float = Field(gt=0)
