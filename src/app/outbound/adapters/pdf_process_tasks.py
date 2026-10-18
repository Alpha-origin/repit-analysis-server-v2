from __future__ import annotations

import math
from io import BytesIO
from typing import Literal

import fitz
from PIL import Image

from app.core.common.interview_qa.dto import ImageBlock, ParsedPortfolio, PdfPage, TextBlock
from app.core.common.interview_qa.pdf_recovery_dto import Region
from app.core.common.interview_qa.ports.pdf_document_processor import PdfProcessingError, RenderedPage

_WHITE = 255
_MAX_IMAGE_BYTES = 4_000_000
_PDF_ERRORS = (RuntimeError, ValueError, fitz.mupdf.FzErrorBase)


def _box(page: fitz.Page, coordinates: tuple[float, ...]) -> Region:
    rect = (fitz.Rect(coordinates) * page.rotation_matrix) & page.rect
    return float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)


def extract_document(pdf_bytes: bytes) -> ParsedPortfolio:
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as document:
            return ParsedPortfolio(pages=[_extract_page(document, number) for number in range(document.page_count)])
    except _PDF_ERRORS as error:
        raise PdfProcessingError("extract_error") from error


def _extract_page(document: fitz.Document, number: int) -> PdfPage:
    try:
        page = document.load_page(number)
    except _PDF_ERRORS:
        return PdfPage(
            page_number=number,
            page_width=1,
            page_height=1,
            text_blocks=[],
            image_blocks=[],
            native_extraction_error="page_load_error",
        )
    text_blocks = []
    extraction_error = None
    try:
        for x0, y0, x1, y1, text, _block_no, kind in page.get_text("blocks"):
            if kind == 0 and text.strip():
                text_blocks.append(TextBlock(text=text.strip(), bbox=_box(page, (x0, y0, x1, y1))))
    except _PDF_ERRORS:
        extraction_error = "native_extraction_error"
    images: list[ImageBlock] = []
    diagnostics = []
    try:
        for info in page.get_images(full=True):
            xref = int(info[0])
            extracted = document.extract_image(xref)
            if not extracted:
                continue
            images.extend(
                ImageBlock(
                    image_bytes=extracted["image"],
                    width=int(extracted.get("width", 0)),
                    height=int(extracted.get("height", 0)),
                    bbox=_box(page, tuple(rect)),
                    xref=xref,
                )
                for rect in page.get_image_rects(xref)
            )
    except _PDF_ERRORS:
        diagnostics.append("image_extraction_error")
    result = PdfPage(
        page_number=number,
        page_width=float(page.rect.width),
        page_height=float(page.rect.height),
        text_blocks=text_blocks,
        raw_text_blocks=[block.model_copy(deep=True) for block in text_blocks],
        image_blocks=images,
        native_extraction_error=extraction_error,
    )
    result.text_recovery.diagnostics = diagnostics
    return result


def render_page(
    pdf_bytes: bytes,
    page_number: int,
    region: Region | None,
    dpi: int,
    max_pixels: int,
    long_edge_px: int,
) -> RenderedPage:
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as document:
            page = document.load_page(page_number)
            visible = page.rect if region is None else fitz.Rect(region) & page.rect
            if visible.is_empty or visible.is_infinite:
                raise PdfProcessingError("render_error")
            display_region = tuple(float(value) for value in visible)
            # Display-list rendering already includes page rotation and CropBox. Its clip
            # uses the same displayed coordinates produced by native rotation_matrix.
            scale = min(dpi / 72, math.sqrt(max_pixels / visible.get_area()) * 0.99)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=visible, alpha=False)
            while pixmap.width * pixmap.height > max_pixels:
                scale *= math.sqrt(max_pixels / (pixmap.width * pixmap.height)) * 0.9
                pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=visible, alpha=False)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            blank = not page.get_bboxlog() and all(
                channel.getextrema() == (_WHITE, _WHITE) for channel in image.split()
            )
            image.thumbnail((long_edge_px, long_edge_px), Image.Resampling.LANCZOS)
            encoded, media_type = _encode(image)
            return RenderedPage(
                image_bytes=encoded,
                media_type=media_type,
                width=image.width,
                height=image.height,
                region=(display_region[0], display_region[1], display_region[2], display_region[3]),
                definitely_blank=blank,
            )
    except _PDF_ERRORS as error:
        raise PdfProcessingError("render_error") from error


def _encode(image: Image.Image) -> tuple[bytes, Literal["image/png", "image/jpeg"]]:
    output = BytesIO()
    image.save(output, format="PNG")
    if output.tell() <= _MAX_IMAGE_BYTES:
        return output.getvalue(), "image/png"
    output = BytesIO()
    image.save(output, format="JPEG", quality=85)
    if output.tell() > _MAX_IMAGE_BYTES:
        raise PdfProcessingError("render_error")
    return output.getvalue(), "image/jpeg"
