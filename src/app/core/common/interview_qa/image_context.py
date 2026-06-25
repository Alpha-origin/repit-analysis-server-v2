
from __future__ import annotations

from app.core.common.interview_qa.dto import ImageBlock, PdfPage


def surrounding_text(
    page: PdfPage,
    img: ImageBlock,
    vertical_px: float,
    max_chars: int,
) -> str:
    img_top, img_bottom = img.bbox[1], img.bbox[3]
    near = [
        tb
        for tb in page.text_blocks
        if abs(tb.bbox[1] - img_bottom) <= vertical_px or abs(tb.bbox[3] - img_top) <= vertical_px
    ]
    if not near:
        # 가까운 블록이 없으면 페이지 위쪽 블록 일부를 컨텍스트로 사용.
        near = page.text_blocks[:2]

    joined = "\n".join(tb.text for tb in near).strip()
    if len(joined) > max_chars:
        joined = joined[:max_chars] + " …"
    return joined
