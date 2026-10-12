from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.core.common.security.url_policy import normalize_hosts
from app.core.common.video.identity import IDENTITY_VERSION
from app.core.common.video.policy import GIB, MIN_RETENTION_DAYS, AcceptedVideoPolicy, VideoLimits
from app.main.config import split_hosts


class AnalyzerSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    backend: Literal["unconfigured", "process"] = "unconfigured"
    executable: Path | None = None
    model_path: Path | None = None
    model_version: str | None = Field(default=None, min_length=1)
    data_schema_version: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def configured(self) -> AnalyzerSettings:
        if self.backend == "process":
            if any(
                value is None
                for value in (self.executable, self.model_path, self.model_version, self.data_schema_version)
            ):
                raise ValueError("process analyzer requires executable, model path and schema/model versions")
            if (self.executable is not None and not self.executable.is_absolute()) or (
                self.model_path is not None and not self.model_path.is_absolute()
            ):
                raise ValueError("analyzer paths must be absolute")
        return self


class VideoSettings(BaseSettings):
    """영상 분석 설정. API·워커·정리 프로세스가 같은 값을 써야 한다(특히 database_path/artifact_root)."""

    model_config = SettingsConfigDict(
        env_prefix="VIDEO_",
        env_file=".env",
        extra="ignore",
        env_nested_delimiter="__",
        hide_input_in_errors=True,
    )

    # 라우터 등록만 제어한다. false 여도 워커·정리 프로세스는 이미 접수된 작업을 끝까지 처리한다.
    enabled: bool = False
    metrics_enabled: bool = False
    database_path: Path = Path("var/video/jobs.sqlite3")
    artifact_root: Path = Path("var/video/artifacts")
    # POST/GET /analysis/video* 호출자 인증용(X-Internal-Token). 발신 콜백 토큰과 별개다.
    api_token: SecretStr | None = None
    # 긴급 차단할 S3 호스트. 이미 접수된 작업에도 다운로드 직전에 적용된다.
    blocked_source_hosts: Annotated[tuple[str, ...], NoDecode] = ()
    disk_budget_bytes: int = Field(default=20 * GIB, gt=0)
    io_concurrency: int = Field(default=2, ge=1)
    cpu_concurrency: int = Field(default=1, ge=1)
    callback_concurrency: int = Field(default=1, ge=1)
    lease_seconds: int = Field(default=180, ge=30)
    stage_max_attempts: int = Field(default=3, ge=1)
    stage_retry_delays_seconds: tuple[int, ...] = (2, 4)
    callback_max_attempts: int = Field(default=6, ge=1)
    callback_retry_delays_seconds: tuple[int, ...] = (5, 15, 60, 300, 900)
    callback_deadline_seconds: int = Field(default=15, gt=0)
    result_retention_days: int = Field(default=90, ge=MIN_RETENTION_DAYS)
    tombstone_retention_days: int = Field(default=90, ge=MIN_RETENTION_DAYS)
    artifact_retention_hours: int = Field(default=24, gt=0)
    policy: VideoLimits = Field(default_factory=VideoLimits)
    analyzer: AnalyzerSettings = Field(default_factory=AnalyzerSettings)

    @field_validator("blocked_source_hosts", mode="before")
    @classmethod
    def _split_hosts(cls, value: object) -> object:
        return split_hosts(value)

    @model_validator(mode="after")
    def _consistent(self) -> VideoSettings:
        if self.enabled:
            token = self.api_token.get_secret_value().strip() if self.api_token is not None else ""
            if not token:
                raise ValueError("VIDEO_API_TOKEN is required when VIDEO_ENABLED=true")
        if self.lease_seconds <= self.callback_deadline_seconds:
            raise ValueError("lease_seconds must exceed the callback deadline")
        if self.disk_budget_bytes < self.policy.max_bytes:
            raise ValueError("disk budget must hold the largest accepted source")
        normalize_hosts(self.blocked_source_hosts)
        normalize_hosts(self.policy.source_hosts)
        # Validates retry schedules and retention against the same rules as the stored snapshot.
        self.accepted_policy(())
        return self

    def accepted_policy(self, callback_hosts: tuple[str, ...]) -> AcceptedVideoPolicy:
        return AcceptedVideoPolicy(
            identity_version=IDENTITY_VERSION,
            limits=self.policy,
            admitted_callback_hosts=tuple(sorted(callback_hosts)),
            stage_max_attempts=self.stage_max_attempts,
            stage_retry_delays_seconds=self.stage_retry_delays_seconds,
            callback_max_attempts=self.callback_max_attempts,
            callback_retry_delays_seconds=self.callback_retry_delays_seconds,
            callback_deadline_seconds=self.callback_deadline_seconds,
            result_retention_days=self.result_retention_days,
            tombstone_retention_days=self.tombstone_retention_days,
            artifact_retention_hours=self.artifact_retention_hours,
        )

    def capacities(self, lanes: tuple[str, ...] = ("io", "cpu")) -> dict[str, int]:
        available = {"io": self.io_concurrency, "cpu": self.cpu_concurrency}
        return {lane: available[lane] for lane in lanes if lane in available}
