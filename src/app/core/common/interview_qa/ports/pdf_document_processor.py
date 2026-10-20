from typing import Literal, Protocol

from pydantic import BaseModel

from app.core.common.interview_qa.dto import ParsedPortfolio
from app.core.common.interview_qa.pdf_recovery_dto import Region


class PdfProcessingError(Exception):
    def __init__(self, kind: Literal["extract_error", "render_error", "processor_busy", "processor_broken"]) -> None:
        super().__init__(kind)
        self.kind = kind


class RenderedPage(BaseModel):
    image_bytes: bytes
    media_type: Literal["image/png", "image/jpeg"] = "image/png"
    width: int
    height: int
    region: Region
    definitely_blank: bool = False


class PdfDocumentProcessor(Protocol):
    async def extract(self, pdf_bytes: bytes) -> ParsedPortfolio: ...

    async def render(
        self,
        pdf_bytes: bytes,
        page_number: int,
        *,
        region: Region | None = None,
        dpi: int = 150,
        max_pixels: int = 12_000_000,
        long_edge_px: int = 1500,
    ) -> RenderedPage: ...
