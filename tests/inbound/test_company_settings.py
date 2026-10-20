import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from dishka import Provider, Scope
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicCallResult, AnthropicTextClient
from app.core.common.interview_qa.ports.webhook_client import WebhookClient
from app.main.audio_config import AudioSettings
from app.main.config import AnthropicSettings, CompanyFitSettings, FeedbackMultiSettings, FeedbackSoloSettings
from app.main.run import make_app
from app.main.video_config import VideoSettings
from app.outbound.adapters.anthropic_text_client_impl import AnthropicTextClientImpl
from tests.company_helpers import RecordingCompanyClient, company_body
from tests.multi_helpers import RecordingWebhook, feedback_body


@pytest.mark.parametrize("mode", ["solo", "multi"])
@pytest.mark.parametrize("model", ["fit-model", None])
async def test_app_wires_fit_settings_and_mode_specific_answer_limit(mode: str, model: str | None) -> None:
    llm = RecordingCompanyClient()
    webhook = RecordingWebhook()
    # 이 테스트는 길이 설정을 확인하므로 잘린 답변 밖의 깊이 근거를 생성하지 않는다.
    for name in ("submit_feedback", "submit_multi_feedback"):
        llm.outputs[name]["feedbacks"][0]["depth_evidence"] = {
            "reason": "",
            "alternatives": "",
            "limitation": "",
            "generic_reason": "",
        }
    provider = Provider()
    provider.provide(lambda: llm, provides=AnthropicTextClient, scope=Scope.APP)
    provider.provide(lambda: webhook, provides=WebhookClient, scope=Scope.APP)
    app = make_app(
        provider,
        anthropic_settings=AnthropicSettings(API_KEY="stub", TEXT_MODEL="feedback-model"),
        company_fit_settings=CompanyFitSettings(MODEL=model, MAX_TOKENS=777, TEMPERATURE=0.1),
        feedback_solo_settings=FeedbackSoloSettings(ANSWER_MAX_CHARS=4),
        feedback_multi_settings=FeedbackMultiSettings(ANSWER_MAX_CHARS=6),
        audio_settings=AudioSettings(enabled=False),
        video_settings=VideoSettings(enabled=False),
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(f"/feedback/{mode}", json=dict(feedback_body(), company=company_body()))
    finally:
        await app.state.dishka_container.close()
    assert response.status_code == 202
    assert webhook.payloads[0]["status"] == "succeeded"
    fit = next(call for call in llm.calls if call["tool_choice"].get("name") == "submit_company_fit")
    assert fit["model"] == (model or "feedback-model")
    assert fit["max_tokens"] == 777
    assert fit["temperature"] == 0.1
    answer = feedback_body()["answers"][0]["content"]
    limit = 4 if mode == "solo" else 6
    assert f"답변: {answer[:limit]}...(이하 생략)" in fit["messages"][0]["content"]


@pytest.mark.parametrize("temperature", [None, 0.2])
async def test_adapter_only_passes_temperature_when_requested(
    temperature: float | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AnthropicTextClientImpl("stub")
    response = AsyncMock()
    response.content = []
    response.usage.input_tokens = 1
    response.usage.output_tokens = 1
    response.stop_reason = "end_turn"
    create = AsyncMock(return_value=response)
    monkeypatch.setattr(client._client.messages, "create", create)
    try:
        kwargs: dict[str, Any] = {}
        if temperature is not None:
            kwargs["temperature"] = temperature
        await client.call(model="stub", system="test", messages=[], **kwargs)
    finally:
        await client.close()
    if temperature is None:
        assert "temperature" not in create.call_args.kwargs
    else:
        assert create.call_args.kwargs["temperature"] == temperature


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TIMEOUT_SECONDS", "0"),
        ("TIMEOUT_SECONDS", "-1"),
        ("TIMEOUT_SECONDS", "nan"),
        ("TIMEOUT_SECONDS", "inf"),
        ("MAX_TOKENS", "0"),
        ("MAX_TOKENS", "-1"),
        ("TEMPERATURE", "-0.1"),
        ("TEMPERATURE", "1.1"),
        ("TEMPERATURE", "nan"),
        ("TEMPERATURE", "inf"),
    ],
)
def test_invalid_company_fit_environment_is_rejected(name: str, value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(f"COMPANY_FIT_{name}", value)
    with pytest.raises(ValidationError):
        CompanyFitSettings(_env_file=None)


@pytest.mark.parametrize("temperature", [0, 1])
def test_company_fit_setting_boundaries_are_allowed(temperature: float) -> None:
    settings = CompanyFitSettings(MAX_TOKENS=1, TEMPERATURE=temperature)
    assert settings.TIMEOUT_SECONDS == 20
    assert settings.MAX_TOKENS == 1
    assert temperature == settings.TEMPERATURE


@pytest.mark.parametrize("mode", ["solo", "multi"])
async def test_app_applies_configured_fit_timeout(mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    llm = RecordingCompanyClient()
    original = llm.call
    cancelled = asyncio.Event()

    async def call(**kwargs: Any) -> AnthropicCallResult:
        if kwargs["tool_choice"].get("name") == "submit_company_fit":
            try:
                await asyncio.Future[None]()
            finally:
                cancelled.set()
        return await original(**kwargs)

    monkeypatch.setattr(llm, "call", call)
    webhook = RecordingWebhook()
    provider = Provider()
    provider.provide(lambda: llm, provides=AnthropicTextClient, scope=Scope.APP)
    provider.provide(lambda: webhook, provides=WebhookClient, scope=Scope.APP)
    app = make_app(
        provider,
        anthropic_settings=AnthropicSettings(API_KEY="stub", TEXT_MODEL="stub"),
        company_fit_settings=CompanyFitSettings(TIMEOUT_SECONDS=0.02),
        audio_settings=AudioSettings(enabled=False),
        video_settings=VideoSettings(enabled=False),
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await asyncio.wait_for(
                client.post(f"/feedback/{mode}", json=dict(feedback_body(), company=company_body())), 2
            )
    finally:
        await app.state.dishka_container.close()
    assert response.status_code == 202
    assert cancelled.is_set()
    assert webhook.payloads[0]["status"] == "succeeded"
    assert webhook.payloads[0]["result"]["companyFit"] is None
