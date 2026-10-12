from __future__ import annotations

import logging

import pytest
from pydantic import SecretStr, ValidationError

from app.main.config import AppSettings, CallbackSecuritySettings
from app.main.security_bootstrap import runtime_security_from
from app.main.video_config import VideoSettings

SECRET = "super-secret-callback-token-value"


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "APP_ENVIRONMENT",
        "APP_INTERNAL_CALLBACK_TOKEN",
        "APP_CALLBACK_ALLOWED_HOSTS",
        "APP_CALLBACK_BLOCKED_HOSTS",
        "VIDEO_ENABLED",
        "VIDEO_API_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)


def test_environment_and_separate_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    default = CallbackSecuritySettings()
    assert default.ENVIRONMENT == "development"  # existing deployments keep booting
    assert default.INTERNAL_CALLBACK_TOKEN is None
    monkeypatch.setenv("APP_ENVIRONMENT", "production")
    monkeypatch.setenv("APP_INTERNAL_CALLBACK_TOKEN", SECRET)
    monkeypatch.setenv("APP_CALLBACK_ALLOWED_HOSTS", "api.repit.example.com, Hooks.Example.com")
    monkeypatch.setenv("VIDEO_ENABLED", "true")
    monkeypatch.setenv("VIDEO_API_TOKEN", "video-request-token")
    settings = CallbackSecuritySettings()
    video = VideoSettings()
    assert settings.CALLBACK_ALLOWED_HOSTS == ("api.repit.example.com", "hooks.example.com")
    assert video.api_token is not None
    # Two independent credentials: inbound video API vs outbound callbacks.
    assert video.api_token.get_secret_value() != settings.INTERNAL_CALLBACK_TOKEN.get_secret_value()  # type: ignore[union-attr]
    assert SECRET not in repr(settings)
    assert SECRET not in str(settings.model_dump())
    assert SECRET not in repr(runtime_security_from(settings).current())
    # DEBUG_MODE never implies production.
    assert "ENVIRONMENT" not in AppSettings.model_fields


def test_production_missing_token_fails_without_leak(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ValidationError) as missing:
        CallbackSecuritySettings(ENVIRONMENT="production")
    assert "required in production" in str(missing.value)
    with pytest.raises(ValidationError) as blank:
        CallbackSecuritySettings(ENVIRONMENT="production", INTERNAL_CALLBACK_TOKEN=SecretStr("   "))
    assert "blank" in str(blank.value)
    with pytest.raises(ValidationError) as bad_host:
        CallbackSecuritySettings(INTERNAL_CALLBACK_TOKEN=SecretStr(SECRET), CALLBACK_ALLOWED_HOSTS=("127.0.0.1",))
    assert SECRET not in str(bad_host.value)
    assert SECRET not in caplog.text
    # Development/test may run without a token.
    assert CallbackSecuritySettings(ENVIRONMENT="test").INTERNAL_CALLBACK_TOKEN is None
