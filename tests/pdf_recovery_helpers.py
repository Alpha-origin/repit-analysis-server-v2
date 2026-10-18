import asyncio
from typing import Any

from app.core.common.interview_qa.dto import ParsedPortfolio, PdfPage, TextBlock
from app.core.common.interview_qa.pdf_recovery_dto import Region
from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicCallResult
from app.core.common.interview_qa.ports.pdf_document_processor import PdfProcessingError, RenderedPage


def page(number: int = 0, text: str = "", bbox: Region = (10, 10, 90, 30)) -> PdfPage:
    blocks = [TextBlock(text=text, bbox=bbox)] if text else []
    return PdfPage(
        page_number=number,
        page_width=100,
        page_height=100,
        text_blocks=blocks,
        raw_text_blocks=[block.model_copy(deep=True) for block in blocks],
        image_blocks=[],
    )


def transcription(text: str = "Recovered 123 ms", **updates: Any) -> dict[str, Any]:
    result = {
        "status": "text_present",
        "blocks": [{"kind": "paragraph", "text": text, "bbox_normalized": [100, 100, 900, 300], "legibility": "clear"}],
        "unreadable_regions": [],
        "needs_zoom": False,
    }
    return result | updates


def response(
    data: dict[str, Any], stop_reason: str = "tool_use", name: str = "transcribe_pdf_page"
) -> AnthropicCallResult:
    return AnthropicCallResult(
        content_blocks=[{"type": "tool_use", "name": name, "input": data}],
        input_tokens=11,
        output_tokens=17,
        stop_reason=stop_reason,
    )


class FakeProcessor:
    def __init__(self, *, blank: set[int] | None = None, failure: PdfProcessingError | None = None) -> None:
        self.blank = blank or set()
        self.failure = failure
        self.calls: list[tuple[int, Region | None, int]] = []

    async def extract(self, pdf_bytes: bytes) -> ParsedPortfolio:
        return ParsedPortfolio(pages=[page()])

    async def render(
        self,
        pdf_bytes: bytes,
        page_number: int,
        *,
        region: Region | None = None,
        dpi: int = 150,
        max_pixels: int = 12_000_000,
        long_edge_px: int = 1500,
    ) -> RenderedPage:
        self.calls.append((page_number, region, dpi))
        if self.failure:
            raise self.failure
        return RenderedPage(
            image_bytes=b"image",
            width=100,
            height=100,
            region=region or (0, 0, 100, 100),
            definitely_blank=page_number in self.blank,
        )


class FakeClient:
    def __init__(self, results: list[AnthropicCallResult | Exception], delay: float = 0) -> None:
        self.results = iter(results)
        self.calls: list[dict[str, Any]] = []
        self.delay = delay
        self.active = 0
        self.peak = 0
        self.cancelled = 0

    async def call(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
        max_tokens: int = 4096,
        temperature: float | None = None,
    ) -> AnthropicCallResult:
        self.calls.append({"model": model, "system": system, "messages": messages, "tool_choice": tool_choice})
        result = next(self.results)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if isinstance(result, Exception):
                raise result
            return result
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            self.active -= 1
