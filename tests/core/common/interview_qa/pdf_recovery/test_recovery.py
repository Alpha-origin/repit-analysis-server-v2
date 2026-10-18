import asyncio
from typing import Literal, cast

import pytest

from app.core.common.interview_qa.dto import ParsedPortfolio
from app.core.common.interview_qa.pdf_page_transcription import PageTextTranscriber
from app.core.common.interview_qa.pdf_recovery_dto import RecoveryOptions
from app.core.common.interview_qa.ports.pdf_document_processor import PdfProcessingError
from app.core.common.interview_qa.stage2_page_text_recovery import Stage2PageTextRecovery
from app.core.common.interview_qa.stage2_text_quality import Stage2TextQuality
from tests.pdf_recovery_helpers import FakeClient, FakeProcessor, page, response, transcription


def stage(client: FakeClient, processor: FakeProcessor, options: RecoveryOptions) -> Stage2PageTextRecovery:
    return Stage2PageTextRecovery(processor, PageTextTranscriber(client, "stub", options), options)


async def test_disabled_and_normal_pages_do_not_render_or_call_vision() -> None:
    processor = FakeProcessor()
    client = FakeClient([])
    parsed = ParsedPortfolio(pages=[page(text="normal " * 100), page(1)])
    plans = await Stage2TextQuality().execute(parsed)
    disabled = await stage(client, processor, RecoveryOptions()).execute(b"p", parsed, plans)
    assert disabled.pages[1].text_recovery.failure_reason == "feature_disabled"
    assert not disabled.pages[1].text_recovery.evaluated
    normal = ParsedPortfolio(pages=[parsed.pages[0]])
    enabled = await stage(client, processor, RecoveryOptions(enabled=True)).execute(b"p", normal, [])
    assert enabled.pages[0].text_blocks == normal.pages[0].text_blocks
    assert enabled.pages[0].text_recovery.state == "not_required"
    assert not client.calls
    assert not processor.calls


@pytest.mark.parametrize("native", ["", "\ufffd" * 10, "TITLE"])
async def test_single_scan_or_broken_page_recovered_without_image_branch(native: str) -> None:
    processor = FakeProcessor()
    client = FakeClient([response(transcription("Body 12.5 ms"))])
    target = page(text=native)
    if native == "TITLE":
        target.text_blocks[0].bbox = (0, 0, 100, 5)
        target.raw_text_blocks[0].bbox = (0, 0, 100, 5)
    parsed = ParsedPortfolio(pages=[target])
    result = await stage(client, processor, RecoveryOptions(enabled=True)).execute(
        b"original", parsed, await Stage2TextQuality().execute(parsed)
    )
    recovered = result.pages[0]
    assert recovered.text_recovery.state == "recovered"
    assert recovered.text_blocks[-1].text == "Body 12.5 ms"
    assert recovered.raw_text_blocks == target.raw_text_blocks
    assert recovered.text_recovery.call_count == 1
    assert recovered.text_blocks[-1].source_region == (0.0, 0.0, 100.0, 100.0)
    assert recovered.text_recovery.output_tokens == 17
    assert len(client.calls) == 1
    assert len(processor.calls) == 2
    assert parsed.pages[0].text_recovery.call_count == 0


async def test_blank_skips_vision_but_figure_and_short_cover_are_no_text_or_native() -> None:
    parsed = ParsedPortfolio(pages=[page(0), page(1), page(2, "Cover")])
    client = FakeClient([response(transcription(status="no_text", blocks=[])), response(transcription("Cover"))])
    result = await stage(client, FakeProcessor(blank={0}), RecoveryOptions(enabled=True)).execute(
        b"p", parsed, await Stage2TextQuality().execute(parsed)
    )
    assert [item.text_recovery.state for item in result.pages] == ["no_text", "no_text", "recovered"]
    assert [item.text_recovery.call_count for item in result.pages] == [0, 1, 1]
    assert [b.text for b in result.pages[2].text_blocks] == ["Cover"]


async def test_zoom_calls_follow_all_initial_pages_and_share_remaining_budget() -> None:
    parsed = ParsedPortfolio(pages=[page(0), page(1)])
    zoom = transcription(status="unreadable", blocks=[], needs_zoom=True)
    client = FakeClient(
        [response(zoom), response(zoom), response(transcription("tile zero")), response(transcription("tile one"))]
    )
    processor = FakeProcessor()
    result = await stage(client, processor, RecoveryOptions(enabled=True, max_calls=4)).execute(
        b"p", parsed, await Stage2TextQuality().execute(parsed)
    )
    assert [item.text_recovery.call_count for item in result.pages] == [2, 2]
    assert [item.text_recovery.state for item in result.pages] == ["partial", "partial"]
    assert [item.text_recovery.failure_reason for item in result.pages] == ["skipped_budget", "skipped_budget"]
    high_resolution_calls = [call for call in processor.calls if call[2] == 150]
    assert [call[0] for call in high_resolution_calls] == [0, 1, 0, 1]
    assert high_resolution_calls[2][1] != (0, 0, 100, 100)


async def test_successful_tiles_resolve_unreadable_full_page_and_exclude_uncertain_text() -> None:
    parsed = ParsedPortfolio(pages=[page()])
    zoom = transcription("DO NOT USE", needs_zoom=True)
    zoom["blocks"][0]["legibility"] = "uncertain"
    client = FakeClient([response(zoom), *[response(transcription(f"tile {i}")) for i in range(4)]])
    result = await stage(client, FakeProcessor(), RecoveryOptions(enabled=True)).execute(
        b"p", parsed, await Stage2TextQuality().execute(parsed)
    )
    recovered = result.pages[0]
    assert recovered.text_recovery.state == "recovered"
    assert "DO NOT USE" not in [block.text for block in recovered.text_blocks]
    assert recovered.text_recovery.uncertain_blocks[0].text == "DO NOT USE"
    assert len(recovered.text_blocks) == 4
    assert not recovered.text_recovery.unreadable_regions


async def test_page_and_call_limits_prioritize_empty_and_never_expose_broken_native() -> None:
    parsed = ParsedPortfolio(pages=[page(0, "Cover"), page(1, "\ufffd" * 10), page(2)])
    client = FakeClient([response(transcription())])
    result = await stage(client, FakeProcessor(), RecoveryOptions(enabled=True, max_pages=2, max_calls=1)).execute(
        b"p", parsed, await Stage2TextQuality().execute(parsed)
    )
    assert result.pages[0].text_recovery.failure_reason == "skipped_budget"
    assert result.pages[1].text_recovery.state == "recovered"
    assert result.pages[2].text_recovery.state == "skipped"
    assert len(client.calls) == 1


@pytest.mark.parametrize("kind", ["render_error", "processor_busy"])
async def test_render_failure_recorded_without_api_call(kind: str) -> None:

    processor = FakeProcessor(failure=PdfProcessingError(cast("Literal['render_error', 'processor_busy']", kind)))
    client = FakeClient([])
    parsed = ParsedPortfolio(pages=[page()])
    result = await stage(client, processor, RecoveryOptions(enabled=True)).execute(
        b"p", parsed, await Stage2TextQuality().execute(parsed)
    )
    assert result.pages[0].text_recovery.state == "failed"
    assert not client.calls


async def test_total_timeout_and_external_cancellation_stop_calls() -> None:
    parsed = ParsedPortfolio(pages=[page(i) for i in range(3)])
    plans = await Stage2TextQuality().execute(parsed)
    client = FakeClient([response(transcription()) for _ in range(3)], delay=0.1)
    result = await stage(
        client, FakeProcessor(), RecoveryOptions(enabled=True, concurrency=2, total_timeout_seconds=0.01)
    ).execute(b"p", parsed, plans)
    assert client.peak == 2
    assert client.active == 0
    assert len(client.calls) == 2
    assert all(item.text_recovery.failure_reason == "timeout" for item in result.pages)
    client2 = FakeClient([response(transcription()) for _ in range(3)], delay=1)
    task = asyncio.create_task(
        stage(client2, FakeProcessor(), RecoveryOptions(enabled=True)).execute(b"p", parsed, plans)
    )
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client2.active == 0


async def test_uncertain_region_is_not_cleared_by_its_own_overlapping_clear_block() -> None:
    data = transcription("Clear paragraph", unreadable_regions=[[100, 100, 200, 200]])
    client = FakeClient([response(data)])
    parsed = ParsedPortfolio(pages=[page()])
    result = await stage(client, FakeProcessor(), RecoveryOptions(enabled=True, max_tiles_per_page=0)).execute(
        b"p", parsed, await Stage2TextQuality().execute(parsed)
    )
    assert result.pages[0].text_recovery.state == "partial"
    assert result.pages[0].text_recovery.unreadable_regions
