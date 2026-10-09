from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.common.audio.dto import AudioPolicy


class AudioSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUDIO_", env_file=".env", extra="ignore", env_nested_delimiter="__")

    enabled: bool = False
    source_root: Path = Path("var/audio/uploads")
    artifact_root: Path = Path("var/audio/artifacts")
    database_path: Path = Path("var/audio/jobs.sqlite3")
    callback_hosts: list[str] = Field(default_factory=list)
    cpu_concurrency: int = Field(default=2, ge=1)
    inference_concurrency: int = Field(default=1, ge=1)
    llm_concurrency: int = Field(default=2, ge=1)
    callback_concurrency: int = Field(default=1, ge=1)
    lease_seconds: int = Field(default=180, ge=15)
    max_attempts: int = Field(default=3, ge=1)
    policy: AudioPolicy = Field(default_factory=AudioPolicy)

    def capacities(self) -> dict[str, int]:
        return {
            # Deliver completed jobs before consuming an endless backlog of new input.
            "callback": self.callback_concurrency,
            "inference": self.inference_concurrency,
            "llm": self.llm_concurrency,
            "cpu": self.cpu_concurrency,
        }
