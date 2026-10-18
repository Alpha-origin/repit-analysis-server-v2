import asyncio
from concurrent.futures.process import BrokenProcessPool
from unittest.mock import AsyncMock, MagicMock

import anthropic
import httpx
import pytest
from pydantic import ValidationError

from app.core.commands.dispatch_applicant_profile import DispatchApplicantProfile
from app.core.common.interview_qa.pdf_page_transcription import PageTextTranscriber
from app.core.common.interview_qa.pdf_recovery_dto import RecoveryOptions
from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicTextClientError
from app.core.common.interview_qa.ports.pdf_document_processor import PdfDocumentProcessor, PdfProcessingError
from app.core.common.interview_qa.stage2_page_text_recovery import Stage2PageTextRecovery
from app.main.audio_config import AudioSettings
from app.main.config import AnthropicSettings, InterviewQaSettings
from app.main.run import make_app
from app.main.video_config import VideoSettings
from app.outbound.adapters.anthropic_text_client_impl import AnthropicTextClientImpl
from app.outbound.adapters.pymupdf_document_processor import PymupdfDocumentProcessor


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (400, "configuration"),
        (401, "configuration"),
        (403, "configuration"),
        (408, "transient"),
        (409, "transient"),
        (429, "transient"),
        (500, "transient"),
        (529, "transient"),
        (418, "unknown"),
    ],
)
async def test_sdk_status_error_translated_in_adapter(status: int, kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("POST", "https://api.example.test")
    error = anthropic.APIStatusError("private response", response=httpx.Response(status, request=request), body={})
    client = AnthropicTextClientImpl("stub")
    call = AsyncMock(side_effect=error)
    monkeypatch.setattr(client._client.messages, "create", call)
    try:
        with pytest.raises(AnthropicTextClientError) as caught:
            await client.call(model="stub", system="test", messages=[])
        assert caught.value.kind == kind
        assert "private response" not in str(caught.value)
        assert call.await_count == 1
    finally:
        await client.close()


async def test_network_error_is_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    client = AnthropicTextClientImpl("stub")
    monkeypatch.setattr(
        client._client.messages,
        "create",
        AsyncMock(side_effect=anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.example.test"))),
    )
    try:
        with pytest.raises(AnthropicTextClientError) as caught:
            await client.call(model="stub", system="test", messages=[])
        assert caught.value.kind == "transient"
    finally:
        await client.close()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MAX_PAGES", "0"),
        ("MAX_CALLS", "-1"),
        ("CONCURRENCY", "0"),
        ("CALL_TIMEOUT_SECONDS", "0"),
        ("MAX_TILES_PER_PAGE", "5"),
        ("RENDER_MAX_PIXELS", "1"),
    ],
)
def test_invalid_settings_rejected(name: str, value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(f"INTERVIEW_QA_PDF_TEXT_RECOVERY_{name}", value)
    with pytest.raises(ValidationError):
        InterviewQaSettings(_env_file=None)


async def test_app_owns_one_processor_and_wires_recovery_config() -> None:
    app = make_app(
        anthropic_settings=AnthropicSettings(API_KEY="stub", VISION_MODEL="vision-stub"),
        interview_qa_settings=InterviewQaSettings(PDF_TEXT_RECOVERY_ENABLED=True, PDF_TEXT_RECOVERY_MAX_CALLS=7),
        audio_settings=AudioSettings(enabled=False),
        video_settings=VideoSettings(enabled=False),
    )
    async with app.router.lifespan_context(app):
        container = app.state.dishka_container
        processor = await container.get(PdfDocumentProcessor)
        async with container() as request1, container() as request2:
            assert await request1.get(PdfDocumentProcessor) is processor
            assert await request2.get(PdfDocumentProcessor) is processor
            assert isinstance(await request1.get(DispatchApplicantProfile), DispatchApplicantProfile)
            options = await request1.get(RecoveryOptions)
            assert options.enabled
            assert options.max_calls == 7
            recovery = await request1.get(Stage2PageTextRecovery)
            transcriber = await request1.get(PageTextTranscriber)
            assert recovery._processor is processor
            assert transcriber._model == "vision-stub"
    assert isinstance(processor, PymupdfDocumentProcessor)
    assert processor._closed


async def test_bounded_queue_and_waiter_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    processor = PymupdfDocumentProcessor(1)
    pending: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    monkeypatch.setattr(asyncio.get_running_loop(), "run_in_executor", MagicMock(return_value=pending))
    active = asyncio.create_task(processor._submit(int, 1))
    waiter = asyncio.create_task(processor._submit(int, 2))
    await asyncio.sleep(0)
    with pytest.raises(PdfProcessingError, match="processor_busy"):
        await processor._submit(int, 3)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert processor._pending == 1
    pending.set_result(1)
    assert await active == 1
    assert processor._pending == 0
    monkeypatch.undo()
    await processor.close()


async def test_broken_pool_recreated_only_once(monkeypatch: pytest.MonkeyPatch) -> None:
    processor = PymupdfDocumentProcessor(1)
    monkeypatch.setattr(asyncio.get_running_loop(), "run_in_executor", MagicMock(side_effect=BrokenProcessPool()))
    try:
        for _ in range(3):
            with pytest.raises(PdfProcessingError, match="processor_broken"):
                await processor._submit(int, 1)
        assert processor._restarted
        assert processor._broken
        assert processor._pending == 0
    finally:
        monkeypatch.undo()
        await processor.close()
