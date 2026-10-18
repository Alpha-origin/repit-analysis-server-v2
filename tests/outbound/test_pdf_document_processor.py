import asyncio
from io import BytesIO
from unittest.mock import MagicMock

import fitz
import pytest
from PIL import Image

from app.core.common.interview_qa.ports.pdf_document_processor import PdfProcessingError
from app.outbound.adapters.pdf_process_tasks import extract_document, render_page
from app.outbound.adapters.pymupdf_document_processor import PymupdfDocumentProcessor


def pdf(*, rotation: int = 0, crop: bool = False, blank: bool = False, faint: bool = False) -> bytes:
    with fitz.open() as document:
        page = document.new_page(width=300, height=400)
        if not blank:
            page.insert_text((70, 100), "ACME 12.5 ms", color=(0.99, 0.99, 0.99) if faint else (0, 0, 0))
        if not blank and not faint:
            page.draw_rect(fitz.Rect(70, 150, 150, 220), color=(1, 0, 0), fill=(1, 0, 0))
        if crop:
            page.set_cropbox(fitz.Rect(30, 40, 270, 360))
        page.set_rotation(rotation)
        return bytes(document.tobytes())


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("crop", [False, True])
def test_native_render_and_clipped_tile_share_display_coordinates(rotation: int, crop: bool) -> None:
    data = pdf(rotation=rotation, crop=crop)
    page = extract_document(data).pages[0]
    assert page.raw_text_blocks[0].text == "ACME 12.5 ms"
    assert page.raw_text_blocks == page.text_blocks
    rendered = render_page(data, 0, None, 72, 12_000_000, 1500)
    with fitz.open(stream=data, filetype="pdf") as doc:
        expected = doc[0].get_pixmap(alpha=False)
        assert (rendered.width, rendered.height) == (expected.width, expected.height)
        assert Image.open(BytesIO(rendered.image_bytes)).convert("RGB").tobytes() == expected.samples
    x0, y0, x1, y1 = page.text_blocks[0].bbox
    assert 0 <= x0 < x1 <= page.page_width
    assert 0 <= y0 < y1 <= page.page_height
    region = (max(0, x0 - 2), max(0, y0 - 2), min(page.page_width, x1 + 2), min(page.page_height, y1 + 2))
    tile = render_page(data, 0, region, 150, 12_000_000, 1500)
    assert tile.region == region
    assert not tile.definitely_blank
    assert Image.open(BytesIO(tile.image_bytes)).convert("L").getextrema() != (255, 255)
    assert max(tile.width, tile.height) <= 1500


def test_blank_confirmation_faint_text_and_raster_pixel_cap() -> None:
    assert render_page(pdf(blank=True), 0, None, 36, 65_536, 256).definitely_blank
    assert not render_page(pdf(faint=True), 0, None, 36, 65_536, 256).definitely_blank
    capped = render_page(pdf(), 0, None, 600, 1024, 1500)
    assert capped.width * capped.height <= 1024


def test_native_page_error_preserves_other_pages_and_programming_errors_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    original = fitz.Page.get_text

    def failing(self: fitz.Page, *args: object, **kwargs: object) -> object:
        if self.number == 0:
            raise RuntimeError("invalid page content")
        return original(self, *args, **kwargs)

    with fitz.open() as document:
        for text in ["Broken", "Good"]:
            document.new_page().insert_text((70, 70), text)
        data = bytes(document.tobytes())
    monkeypatch.setattr(fitz.Page, "get_text", failing)
    parsed = extract_document(data)
    assert parsed.pages[0].native_extraction_error == "native_extraction_error"
    assert parsed.pages[1].text_blocks[0].text == "Good"
    monkeypatch.setattr(fitz.Page, "get_text", MagicMock(side_effect=TypeError("programming error")))
    with pytest.raises(TypeError, match="programming error"):
        extract_document(data)


async def test_shared_process_pool_extracts_renders_and_closes() -> None:
    processor = PymupdfDocumentProcessor(1)
    try:
        parsed = await processor.extract(pdf())
        first, second = await asyncio.gather(processor.render(pdf(), 0), processor.render(pdf(blank=True), 0))
        assert parsed.pages[0].text_blocks[0].text == "ACME 12.5 ms"
        assert not first.definitely_blank
        assert second.definitely_blank
    finally:
        await processor.close()
    with pytest.raises(PdfProcessingError, match="processor_broken"):
        await processor.render(pdf(), 0)


async def test_cancelled_request_keeps_worker_slot_until_process_finishes(monkeypatch: pytest.MonkeyPatch) -> None:
    processor = PymupdfDocumentProcessor(1)
    pending: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    monkeypatch.setattr(asyncio.get_running_loop(), "run_in_executor", MagicMock(return_value=pending))
    task = asyncio.create_task(processor._submit(int, 1))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert processor._pending == 1
    assert processor._available.locked()
    pending.set_result(1)
    await asyncio.sleep(0)
    assert processor._pending == 0
    assert not processor._available.locked()
    monkeypatch.undo()
    await processor.close()
