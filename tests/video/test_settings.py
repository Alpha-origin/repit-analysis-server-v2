from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from app.core.common.video.policy import AcceptedVideoPolicy, VideoLimits
from app.main.audio_config import AudioSettings
from app.main.video_config import VideoSettings


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("VIDEO_ENABLED", "VIDEO_API_TOKEN", "VIDEO_POLICY__MAX_BYTES", "VIDEO_BLOCKED_SOURCE_HOSTS"):
        monkeypatch.delenv(name, raising=False)


def test_defaults_and_policy_snapshot() -> None:
    settings = VideoSettings()
    assert settings.enabled is False
    assert settings.database_path == Path("var/video/jobs.sqlite3")
    assert settings.artifact_root == Path("var/video/artifacts")
    assert (settings.io_concurrency, settings.cpu_concurrency, settings.callback_concurrency) == (2, 1, 1)
    assert settings.lease_seconds == 180
    assert (settings.stage_max_attempts, settings.stage_retry_delays_seconds) == (3, (2, 4))
    assert (settings.callback_max_attempts, settings.callback_retry_delays_seconds) == (6, (5, 15, 60, 300, 900))
    assert settings.callback_deadline_seconds == 15
    assert (settings.result_retention_days, settings.tombstone_retention_days) == (90, 90)
    assert settings.artifact_retention_hours == 24
    limits = settings.policy
    assert (limits.max_bytes, limits.max_duration_ms) == (1_000_000_000, 3_600_000)
    assert (limits.max_long_edge, limits.max_short_edge, limits.max_fps) == (1920, 1080, 60)
    assert (limits.download_timeout_seconds, limits.probe_timeout_seconds, limits.decode_timeout_seconds) == (
        900,
        30,
        3600,
    )
    snapshot = settings.accepted_policy(("b.example.com", "a.example.com"))
    assert snapshot.admitted_callback_hosts == ("a.example.com", "b.example.com")
    dumped = snapshot.model_dump_json()
    assert "token" not in dumped.lower()
    assert "blocked" not in dumped.lower()


def test_nested_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIDEO_POLICY__MAX_BYTES", "500000000")
    monkeypatch.setenv("VIDEO_BLOCKED_SOURCE_HOSTS", "a.s3.amazonaws.com,b.s3.amazonaws.com")
    settings = VideoSettings()
    assert settings.policy.max_bytes == 500_000_000
    assert settings.blocked_source_hosts == ("a.s3.amazonaws.com", "b.s3.amazonaws.com")


@pytest.mark.parametrize(
    "overrides",
    [
        {"result_retention_days": 89},
        {"tombstone_retention_days": 30},
        {"artifact_retention_hours": 0},
        {"stage_retry_delays_seconds": (2,)},
        {"callback_retry_delays_seconds": (5, 15, 60)},
        {"callback_retry_delays_seconds": (5, 15, 60, 300, -1)},
        {"lease_seconds": 15},
        {"policy": {"max_short_edge": 4000}},
        {"policy": {"max_bytes": 0}},
        {"enabled": True},  # no api token
        {"enabled": True, "api_token": SecretStr("  ")},
        {"blocked_source_hosts": ("127.0.0.1",)},
    ],
)
def test_invalid_limits_retention_and_secrets(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        VideoSettings(**overrides)  # type: ignore[arg-type]


def test_snapshot_rejects_secrets_and_short_retention() -> None:
    base = VideoSettings().accepted_policy(()).model_dump()
    with pytest.raises(ValidationError):
        AcceptedVideoPolicy.model_validate({**base, "callback_token": "secret"})
    with pytest.raises(ValidationError):
        AcceptedVideoPolicy.model_validate({**base, "result_retention_days": 30})
    with pytest.raises(ValidationError):
        VideoLimits.model_validate({"api_token": "x"})


def test_video_settings_do_not_change_audio_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    before = AudioSettings().model_dump()
    monkeypatch.setenv("VIDEO_ENABLED", "true")
    monkeypatch.setenv("VIDEO_API_TOKEN", "t")
    monkeypatch.setenv("VIDEO_LEASE_SECONDS", "600")
    VideoSettings()
    assert AudioSettings().model_dump() == before
