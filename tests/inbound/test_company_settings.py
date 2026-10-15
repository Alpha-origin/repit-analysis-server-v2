from typing import Any
from unittest.mock import AsyncMock

import pytest
from dishka import Provider, Scope
from httpx import ASGITransport, AsyncClient

from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicTextClient
from app.core.common.interview_qa.ports.webhook_client import WebhookClient
from app.main.audio_config import AudioSettings
from app.main.config import AnthropicSettings, CompanyFitSettings, FeedbackMultiSettings, FeedbackSoloSettings
from app.main.run import make_app
from app.main.video_config import VideoSettings
from app.outbound.adapters.anthropic_text_client_impl import AnthropicTextClientImpl
from tests.company_helpers import RecordingCompanyClient, company_body
from tests.multi_helpers import RecordingWebhook, feedback_body


@pytest.mark.parametrize("mode", ["solo", "multi"])
async def test_app_wires_fit_settings_and_mode_specific_answer_limit(mode: str) -> None:
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
        anthropic_settings=AnthropicSettings(API_KEY="stub"),
        company_fit_settings=CompanyFitSettings(MODEL="fit-model", MAX_TOKENS=777, TEMPERATURE=0.1),
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
    assert fit["model"] == "fit-model"
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
