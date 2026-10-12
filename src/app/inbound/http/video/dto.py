"""Wire DTO for ``POST /analysis/video``.

Structural checks only — they never depend on server policy, so an already accepted request can always
be replayed. Size/host limits for *new* requests are applied later by admission.
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from app.core.common.security.url_policy import UrlPolicyError, parse_https_url, query_pairs
from app.core.common.video.dto import VideoRequest, VideoSource

_INSTANT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?(Z|[+-]\d{2}:\d{2})$")
_MIME = re.compile(r"^video/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,126}$")


class StrictWire(BaseModel):
    # camelCase only (snake_case keys are unknown fields), no coercion, no extra keys at any level.
    model_config = ConfigDict(
        populate_by_name=False,
        extra="forbid",
        strict=True,
        frozen=True,
        hide_input_in_errors=True,
    )


def _nonblank(value: str) -> str:
    # 원문을 trim 하지 않는다. 공백뿐인 값만 거부한다.
    if not value.strip():
        raise ValueError("must not be blank")
    return value


def _structural_url(value: str) -> str:
    try:
        parsed = parse_https_url(value)
        query_pairs(parsed.query)
    except UrlPolicyError as exc:
        raise ValueError(f"invalid URL ({exc.reason})") from exc
    return value


class VideoPayload(StrictWire):
    video_id: StrictStr = Field(alias="videoId", min_length=1, max_length=200)
    file_url: StrictStr = Field(alias="fileUrl", min_length=1, max_length=16384)
    content_type: StrictStr = Field(alias="contentType")
    file_size: StrictInt = Field(alias="fileSize", gt=0)
    uploaded_at: StrictStr = Field(alias="uploadedAt")

    _video_id = field_validator("video_id")(_nonblank)
    _file_url = field_validator("file_url")(_structural_url)

    @field_validator("content_type")
    @classmethod
    def _mime(cls, value: str) -> str:
        if not _MIME.match(value):
            raise ValueError("must be a video/* media type")
        return value

    @field_validator("uploaded_at")
    @classmethod
    def _aware_instant(cls, value: str) -> str:
        if not _INSTANT.match(value):
            raise ValueError("must be an ISO 8601 date-time with a timezone")
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value


class VideoAnalysisRequest(StrictWire):
    request_id: StrictStr = Field(alias="requestId", min_length=1, max_length=200)
    session_id: StrictStr = Field(alias="sessionId", min_length=1, max_length=200)
    interview_id: StrictStr = Field(alias="interviewId", min_length=1, max_length=200)
    user_id: StrictStr = Field(alias="userId", min_length=1, max_length=200)
    callback_url: StrictStr = Field(alias="callbackUrl", min_length=1, max_length=4096)
    video: VideoPayload = Field(alias="video")

    _ids = field_validator("request_id", "session_id", "interview_id", "user_id")(_nonblank)
    _callback = field_validator("callback_url")(_structural_url)

    def to_request(self) -> VideoRequest:
        return VideoRequest(
            request_id=self.request_id,
            session_id=self.session_id,
            interview_id=self.interview_id,
            user_id=self.user_id,
            callback_url=self.callback_url,
            video=VideoSource(
                video_id=self.video.video_id,
                file_url=self.video.file_url,
                content_type=self.video.content_type,
                file_size=self.video.file_size,
                uploaded_at=self.video.uploaded_at,
            ),
        )
