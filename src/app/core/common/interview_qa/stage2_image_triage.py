
from __future__ import annotations

import logging

from app.core.common.interview_qa.dto import (
    ImageBlock,
    ParsedPortfolio,
    PdfBranch,
    PdfPage,
    TriagedPortfolio,
)

logger = logging.getLogger(__name__)


class Stage2ImageTriage:


    def __init__(
        self,
        min_px: int,
        max_aspect_ratio: float,
        min_area_ratio: float,
        info_img_threshold: int,
        repeated_min_ratio: float,
    ) -> None:
        # 모든 임계값은 CoreProvider 가 Settings 에서 평문으로 풀어 전달한다.
        self._min_px = min_px
        self._max_aspect_ratio = max_aspect_ratio
        self._min_area_ratio = min_area_ratio
        self._info_img_threshold = info_img_threshold
        self._repeated_min_ratio = repeated_min_ratio

    async def execute(self, parsed: ParsedPortfolio) -> TriagedPortfolio:
        result = self._triage(parsed)

        # 종료 로그 — INFO 메타 / DEBUG 풀.
        logger.info(
            "stage2_image_triage.done",
            extra={
                "input_images": sum(len(p.image_blocks) for p in parsed.pages),
                "info_images": result.info_img_count,
                "branch": result.branch,
                "page_count": len(parsed.pages),
            },
        )
        logger.debug(
            "stage2_image_triage.payload",
            extra={
                "per_page_kept": [{"page": p.page_number, "image_count": len(p.image_blocks)} for p in result.pages],
            },
        )
        return result

    # ---------------- 내부 ----------------

    def _triage(self, parsed: ParsedPortfolio) -> TriagedPortfolio:
        total_pages = len(parsed.pages)

        # (1) 같은 xref 가 등장한 페이지 집합 계산.
        # 다른 페이지에 동일 이미지(같은 xref) 가 반복되면 머리/꼬리 로고로 본다.
        xref_pages: dict[int, set[int]] = {}
        for page in parsed.pages:
            for img in page.image_blocks:
                xref_pages.setdefault(img.xref, set()).add(page.page_number)

        # (2) 반복 등장 임계 — 최소 2페이지 + 전체의 일정 비율 이상.
        # 1페이지 문서에서 이 규칙이 발동하지 않도록 가드.
        repeated_threshold = max(2, int(total_pages * self._repeated_min_ratio))
        repeated_xrefs = {xref for xref, pages in xref_pages.items() if len(pages) >= repeated_threshold}

        # (3) 페이지별로 이미지 필터링.
        new_pages: list[PdfPage] = []
        info_count = 0
        for page in parsed.pages:
            page_area = page.page_width * page.page_height
            kept: list[ImageBlock] = []
            for img in page.image_blocks:
                if self._is_decorative(img, page_area, repeated_xrefs):
                    continue
                kept.append(img)
            info_count += len(kept)
            new_pages.append(
                PdfPage(
                    page_number=page.page_number,
                    page_width=page.page_width,
                    page_height=page.page_height,
                    text_blocks=page.text_blocks,
                    image_blocks=kept,
                )
            )

        branch: PdfBranch = "image_heavy" if info_count >= self._info_img_threshold else "text_heavy"
        return TriagedPortfolio(pages=new_pages, branch=branch, info_img_count=info_count)

    def _is_decorative(
        self,
        img: ImageBlock,
        page_area: float,
        repeated_xrefs: set[int],
    ) -> bool:

        # 머리/꼬리 로고 — 같은 이미지가 여러 페이지에 반복.
        if img.xref in repeated_xrefs:
            return True

        # 크기 메타가 빠진 이미지는 신뢰할 수 없어 장식으로 간주(보수적 처리).
        if img.width <= 0 or img.height <= 0:
            return True

        # 짧은 변이 너무 작으면 아이콘류.
        short_edge = min(img.width, img.height)
        if short_edge < self._min_px:
            return True

        # 종횡비가 너무 큰 경우 — 가로 띠/세로 선.
        long_edge = max(img.width, img.height)
        if (long_edge / short_edge) > self._max_aspect_ratio:
            return True

        # 페이지 위에서 차지하는 면적이 너무 작으면 우표 크기.
        if page_area > 0:
            bbox_area = max(0.0, (img.bbox[2] - img.bbox[0]) * (img.bbox[3] - img.bbox[1]))
            if (bbox_area / page_area) < self._min_area_ratio:
                return True

        return False
