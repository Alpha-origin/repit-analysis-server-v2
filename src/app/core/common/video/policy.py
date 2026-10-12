from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MIN_RETENTION_DAYS = 90
GIB = 1024**3


class VideoLimits(BaseModel):
    """Operator-tunable media limits. Copied into every accepted job; later edits never touch old jobs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_hosts: tuple[str, ...] = ()
    max_bytes: int = Field(default=1_000_000_000, gt=0)
    max_duration_ms: int = Field(default=3_600_000, gt=0)
    max_long_edge: int = Field(default=1920, gt=0)
    max_short_edge: int = Field(default=1080, gt=0)
    max_fps: int = Field(default=60, gt=0)
    download_timeout_seconds: int = Field(default=900, gt=0)
    probe_timeout_seconds: int = Field(default=30, gt=0)
    decode_timeout_seconds: int = Field(default=3600, gt=0)
    # Default permits policy-v1 snapshots accepted before analyzer timeouts were introduced.
    analyze_timeout_seconds: int = Field(default=900, gt=0)
    resource_wait_timeout_seconds: int = Field(default=900, gt=0)
    max_process_rss_bytes: int = Field(default=2 * GIB, gt=0)
    max_job_disk_bytes: int = Field(default=2 * GIB, gt=0)
    min_free_disk_bytes: int = Field(default=2 * GIB, ge=0)

    @model_validator(mode="after")
    def edges(self) -> VideoLimits:
        if self.max_short_edge > self.max_long_edge:
            raise ValueError("max_short_edge cannot exceed max_long_edge")
        if self.max_bytes > self.max_job_disk_bytes:
            raise ValueError("the job disk budget must hold the largest accepted source")
        return self


class AcceptedVideoPolicy(BaseModel):
    """Frozen snapshot stored with the job at acceptance time.

    Contains no secrets and no emergency block lists: credentials and blocks are always read from the
    running process right before network I/O.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["video-policy-v1"] = "video-policy-v1"
    identity_version: str
    limits: VideoLimits
    admitted_callback_hosts: tuple[str, ...]
    stage_max_attempts: int = Field(ge=1)
    stage_retry_delays_seconds: tuple[int, ...]
    callback_max_attempts: int = Field(ge=1)
    callback_retry_delays_seconds: tuple[int, ...]
    callback_deadline_seconds: int = Field(gt=0)
    result_retention_days: int = Field(ge=MIN_RETENTION_DAYS)
    tombstone_retention_days: int = Field(ge=MIN_RETENTION_DAYS)
    artifact_retention_hours: int = Field(gt=0)

    @model_validator(mode="after")
    def schedules(self) -> AcceptedVideoPolicy:
        if len(self.stage_retry_delays_seconds) != self.stage_max_attempts - 1:
            raise ValueError("stage retry delays must have one entry per retry")
        if len(self.callback_retry_delays_seconds) != self.callback_max_attempts - 1:
            raise ValueError("callback retry delays must have one entry per retry")
        if any(delay < 0 for delay in (*self.stage_retry_delays_seconds, *self.callback_retry_delays_seconds)):
            raise ValueError("retry delays must not be negative")
        return self
