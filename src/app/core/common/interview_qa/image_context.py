"""이미지 주변 텍스트 추출 공통 헬퍼.

Stage 2-3(LLM 트리아지) 과 Stage 2-4(비전 구조화) 모두에서 같은 규칙으로
이미지의 캡션·제목을 추정해 컨텍스트로 사용한다.
"""

from __future__ import annotations

from app.core.common.interview_qa.dto import ImageBlock, PdfPage


def surrounding_text(
    page: PdfPage,
    img: ImageBlock,
    vertical_px: float,
    max_chars: int,
) -> str:
    """이미지 위/아래 ``vertical_px`` 안에 있는 텍스트 블록을 모은다.

    - 매칭되는 블록이 하나도 없으면 페이지 상위 두 블록을 fallback 으로 사용.
    - 길이가 ``max_chars`` 를 넘으면 잘라낸다(`` …`` 표기).
    """
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
