from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from app.core.common.dto import CamelModel


class AudioAnswer(CamelModel):
    answer_id: str = Field(min_length=1, max_length=200)
    question_id: str = Field(min_length=1, max_length=200)
    asset_key: str = Field(min_length=1, max_length=1000)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    end_reason: Literal["user", "timeout", "interrupted", "unknown"] = "unknown"


class AudioRequest(CamelModel):
    request_id: str = Field(min_length=1, max_length=200)
    session_id: str = Field(min_length=1, max_length=200)
    answers: list[AudioAnswer] = Field(min_length=1, max_length=12)
    callback_url: str | None = None

    @model_validator(mode="after")
    def unique_answers(self) -> AudioRequest:
        if len({answer.answer_id for answer in self.answers}) != len(self.answers):
            raise ValueError("answerId must be unique")
        return self


class AudioPolicy(BaseModel):
    """Persisted with each job; never use a worker's changed policy on an old job."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    version: str = "audio-v1"
    max_bytes: int = Field(default=100_000_000, gt=0)
    max_duration_ms: int = Field(default=240_000, gt=0)
    media_timeout_seconds: int = Field(default=120, gt=0)
    sample_rate: Literal[16000] = 16000
    vad_threshold: float = Field(default=0.5, gt=0, lt=1)
    vad_min_speech_ms: int = Field(default=100, ge=0)
    vad_min_silence_ms: int = Field(default=100, ge=0)
    whisper_model: str = "large-v3"
    whisper_device: Literal["cpu", "cuda"] = "cpu"
    whisper_compute_type: str = "int8"
    whisper_local_only: bool = True
    language: Literal["ko"] = "ko"
    pause_min_ms: int = Field(default=200, ge=0)
    window_ms: int = Field(default=5000, gt=0)
    minimum_window_speech_ms: int = Field(default=1000, gt=0)
    fluency_model: str = "claude-sonnet-4-6"
    fluency_max_tokens: int = Field(default=4096, gt=0)

    def fingerprint(self) -> str:
        return digest(self.model_dump())


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def wire_payload(value: Any) -> Any:
    """Keep analysis contracts in snake_case; apply the server's camelCase at HTTP boundaries."""
    if isinstance(value, dict):
        return {to_camel(key): wire_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [wire_payload(item) for item in value]
    return value


class Region(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self) -> Region:
        if self.end_ms <= self.start_ms:
            raise ValueError("region must have positive duration")
        return self


class TimedWord(BaseModel):
    text: str
    start_ms: int | None = None
    end_ms: int | None = None
    timing_status: Literal["valid", "missing", "invalid"] = "missing"


class Transcript(BaseModel):
    text: str
    words: list[TimedWord] = Field(default_factory=list)
    segments: list[dict[str, Any]] = Field(default_factory=list)
    model: str
    language: str = "ko"


class PreparedAnswerAudio(BaseModel):
    schema_version: str = "1"
    answer_id: str
    question_id: str
    source_checksum: str
    policy_fingerprint: str
    status: Literal["ready", "partial", "unusable"]
    duration_ms: int | None = None
    normalized_ref: str | None = None
    quality: dict[str, Any] | None = None
    speech: list[Region] | None = None
    non_speech: list[Region] | None = None
    transcript: Transcript | None = None
    stage_statuses: dict[str, str]
    versions: dict[str, str] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)


class AudioError(Exception):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class IdempotencyConflictError(Exception):
    pass
