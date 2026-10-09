from __future__ import annotations

from pydantic import AwareDatetime, ConfigDict, Field, model_validator

from app.core.common.audio.dto import AudioAnswer, AudioRequest
from app.core.common.dto import CamelModel


class RecordingRequest(CamelModel):
    model_config = ConfigDict(extra="forbid")
    recording_id: str = Field(min_length=1, max_length=200)
    question_id: str = Field(min_length=1, max_length=200)
    answer_id: str = Field(min_length=1, max_length=200)
    file_url: str = Field(min_length=1, max_length=16384)
    content_type: str = Field(pattern=r"^audio/[A-Za-z0-9.+-]+$")
    file_size: int = Field(gt=0, le=100_000_000)
    uploaded_at: AwareDatetime
    end_reason: str = Field(default="unknown", pattern=r"^(user|timeout|interrupted|unknown)$")


class AudioAnalysisRequest(CamelModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=200)
    session_id: str = Field(min_length=1, max_length=200)
    interview_id: str = Field(min_length=1, max_length=200)
    user_id: str = Field(min_length=1, max_length=200)
    callback_url: str = Field(min_length=1, max_length=4096)
    recordings: list[RecordingRequest] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def unique_recordings(self) -> AudioAnalysisRequest:
        for field in ("recording_id", "answer_id"):
            if len({getattr(item, field) for item in self.recordings}) != len(self.recordings):
                raise ValueError(f"{field} must be unique")
        return self

    def to_job(self) -> AudioRequest:
        return AudioRequest(
            request_id=self.request_id,
            session_id=self.session_id,
            interview_id=self.interview_id,
            user_id=self.user_id,
            callback_url=self.callback_url,
            transport_version=2,
            answers=[
                AudioAnswer.model_validate(
                    {
                        **item.model_dump(exclude={"uploaded_at"}),
                        "uploaded_at": item.uploaded_at.isoformat(),
                    }
                )
                for item in self.recordings
            ],
        )
