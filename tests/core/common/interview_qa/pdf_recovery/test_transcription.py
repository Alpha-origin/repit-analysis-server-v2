from copy import deepcopy
from typing import cast

import pytest

from app.core.common.interview_qa.pdf_page_transcription import PageTextTranscriber
from app.core.common.interview_qa.pdf_recovery_dto import ApiErrorKind, RecoveryOptions
from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicTextClientError
from tests.pdf_recovery_helpers import FakeClient, FakeProcessor, response, transcription


async def test_image_forced_tool_original_numbers_and_table_reading_order() -> None:
    text = "| 항목 | 값 |\n|---|---|\n|지연|12.50 ms|\nACME-123"
    data = transcription(text)
    data["blocks"][0]["kind"] = "table"
    client = FakeClient([response(data)])
    image = await FakeProcessor().render(b"pdf", 0)
    attempt = await PageTextTranscriber(client, "vision-model", RecoveryOptions()).execute(image, 0)
    assert attempt.result is not None
    assert attempt.result.blocks[0].text == text
    assert attempt.failure is None
    assert (attempt.input_tokens, attempt.output_tokens) == (11, 17)
    call = client.calls[0]
    assert call["model"] == "vision-model"
    assert call["tool_choice"] == {"type": "tool", "name": "transcribe_pdf_page"}
    assert call["messages"][0]["content"][0]["type"] == "image"
    assert "추정" in call["system"]


@pytest.mark.parametrize("change", ["bbox", "extra", "missing", "empty", "status", "nan", "legibility"])
async def test_malformed_output_rejected_but_usage_counted(change: str) -> None:
    data = deepcopy(transcription())
    if change == "bbox":
        data["blocks"][0]["bbox_normalized"] = [900, 10, 100, 30]
    elif change == "nan":
        data["blocks"][0]["bbox_normalized"] = [0, 0, float("nan"), 300]
    elif change == "extra":
        data["page_number"] = 9
    elif change == "missing":
        del data["needs_zoom"]
    elif change == "empty":
        data["blocks"][0]["text"] = " "
    elif change == "status":
        data["status"] = "no_text"
    else:
        data["blocks"][0]["legibility"] = "certain"
    client = FakeClient([response(data)])
    attempt = await PageTextTranscriber(client, "stub", RecoveryOptions()).execute(
        await FakeProcessor().render(b"p", 0), 0
    )
    assert attempt.failure == "invalid_response"
    assert attempt.result is None
    assert attempt.output_tokens == 17


@pytest.mark.parametrize(
    ("stop", "name", "failure"),
    [
        ("max_tokens", "transcribe_pdf_page", "truncated"),
        ("end_turn", "transcribe_pdf_page", "invalid_response"),
        ("tool_use", "other", "invalid_response"),
    ],
)
async def test_stop_reason_and_tool_name_are_validated(stop: str, name: str, failure: str) -> None:
    client = FakeClient([response(transcription(), stop, name)])
    attempt = await PageTextTranscriber(client, "stub", RecoveryOptions()).execute(
        await FakeProcessor().render(b"p", 0), 0
    )
    assert attempt.failure == failure
    assert attempt.input_tokens == 11


@pytest.mark.parametrize("kind", ["transient", "configuration", "unknown"])
async def test_error_kind_and_backwards_compatible_exception(kind: str) -> None:

    error = AnthropicTextClientError("private detail", kind=cast("ApiErrorKind", kind))
    client = FakeClient([error])
    attempt = await PageTextTranscriber(client, "stub", RecoveryOptions()).execute(
        await FakeProcessor().render(b"p", 0), 0
    )
    assert attempt.error_kind == kind
    assert attempt.failure == "api_error"
    assert len(client.calls) == 1
    assert AnthropicTextClientError("legacy").kind == "unknown"


async def test_call_timeout_includes_whole_client_call_and_cancels_it() -> None:
    client = FakeClient([response(transcription())], delay=0.1)
    options = RecoveryOptions(call_timeout_seconds=0.01)
    attempt = await PageTextTranscriber(client, "stub", options).execute(await FakeProcessor().render(b"p", 0), 0)
    assert attempt.failure == "timeout"
    assert client.cancelled == 1
