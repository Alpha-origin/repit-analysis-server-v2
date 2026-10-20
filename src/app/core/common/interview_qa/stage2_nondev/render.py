from __future__ import annotations

import fitz

from app.core.common.interview_qa.stage2_nondev.dto import ReadRegion


def render_region(pdf: bytes, region: ReadRegion, long_edge: int) -> bytes:
    with fitz.open(stream=pdf, filetype="pdf") as document:
        page = document[region.page - 1]
        clip = fitz.Rect(region.bbox)
        scale = long_edge / max(clip.width, clip.height)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False, colorspace=fitz.csRGB)
        return bytes(pixmap.tobytes("png"))
