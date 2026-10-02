"""Shared callback security must not change legacy retry behaviour (2 attempts, any non-2xx retried)."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest

from app.core.commands.deliver_video_callback import classify
from app.core.common.security.dto import AttemptOutcome
from app.main.log_redaction import install_log_redaction
from app.outbound.adapters.httpx_webhook_client import HttpxWebhookClient
from tests.video.helpers import CALLBACK_TOKEN, CALLBACK_URL, security


class Statuses:
    def __init__(self, *statuses: int) -> None:
        self.statuses = list(statuses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.statuses.pop(0) if self.statuses else 200, text="secret-body-preview")


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr("app.outbound.adapters.httpx_webhook_client.asyncio.sleep", fake_sleep)
    return recorded


@pytest.mark.asyncio
async def test_legacy_attempt_counts_and_delays(sleeps: list[float]) -> None:
    for statuses, expected_ok, expected_calls in (((200,), True, 1), ((500, 200), True, 2), ((500, 503), False, 2)):
        handler = Statuses(*statuses)
        client = HttpxWebhookClient(15, 5, transport=httpx.MockTransport(handler), security=security())
        assert await client.send(CALLBACK_URL, {"jobId": "j"}) is expected_ok
        assert len(handler.requests) == expected_calls
        assert all(request.headers["x-internal-token"] == CALLBACK_TOKEN for request in handler.requests)
    assert sleeps == [5, 5]  # one fixed delay between the two attempts, unchanged


@pytest.mark.asyncio
async def test_video_policy_does_not_change_legacy_retries(sleeps: list[float]) -> None:
    handler = Statuses(400, 400)
    client = HttpxWebhookClient(15, 2, transport=httpx.MockTransport(handler), security=security())
    assert await client.send(CALLBACK_URL, {"jobId": "j"}) is False
    assert len(handler.requests) == 2  # legacy still retries a 400 ...
    assert classify(AttemptOutcome(kind="http_status", status_code=400)) == "failed"  # ... video does not
    assert sleeps == [2]


@pytest.mark.asyncio
async def test_untrusted_legacy_destination_has_zero_sends(sleeps: list[float]) -> None:
    handler = Statuses()
    production = security(environment="production")
    client = HttpxWebhookClient(15, 2, transport=httpx.MockTransport(handler), security=production)
    assert await client.send("https://unknown.example.com/cb", {"jobId": "j"}) is False
    blocked = security(environment="development", blocked_callback_hosts=("api.repit.example.com",))
    client = HttpxWebhookClient(15, 2, transport=httpx.MockTransport(handler), security=blocked)
    assert await client.send(CALLBACK_URL, {"jobId": "j"}) is False
    assert handler.requests == []
    assert sleeps == []


@pytest.mark.asyncio
async def test_development_default_keeps_existing_local_receivers(sleeps: list[float]) -> None:
    handler = Statuses()
    client = HttpxWebhookClient(15, 2, transport=httpx.MockTransport(handler))
    assert await client.send("http://localhost:8000/mock/callback", {"jobId": "j"}) is True
    assert "x-internal-token" not in handler.requests[0].headers


@pytest.mark.asyncio
async def test_signed_url_and_token_never_reach_logs(sleeps: list[float], caplog: pytest.LogCaptureFixture) -> None:
    # Same logging setup as make_app/workers: redaction filter on every handler, httpx at WARNING.
    install_log_redaction()
    caplog.set_level(logging.DEBUG, logger="app")
    handler = Statuses(500, 500)
    client = HttpxWebhookClient(15, 2, transport=httpx.MockTransport(handler), security=security())
    signed = CALLBACK_URL + "?X-Amz-Signature=abcdef"
    await client.send(signed, {"jobId": "j"})
    records = " ".join(f"{record.getMessage()} {record.__dict__}" for record in caplog.records)
    assert "abcdef" not in records
    assert CALLBACK_TOKEN not in records
    assert "secret-body-preview" not in records


def test_audio_worker_production_missing_token_fails_before_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio  # noqa: PLC0415

    from pydantic import ValidationError  # noqa: PLC0415

    from app.main import audio_worker  # noqa: PLC0415

    monkeypatch.setenv("APP_ENVIRONMENT", "production")
    monkeypatch.delenv("APP_INTERNAL_CALLBACK_TOKEN", raising=False)
    monkeypatch.setenv("AUDIO_DATABASE_PATH", str(tmp_path / "audio.sqlite3"))
    with pytest.raises(ValidationError):
        asyncio.run(audio_worker.run(once=True))
    assert not (tmp_path / "audio.sqlite3").exists()
