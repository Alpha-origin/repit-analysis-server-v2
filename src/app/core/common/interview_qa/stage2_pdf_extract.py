"""Stage 2-1 — PDF 텍스트·이미지 추출 + 규칙 기반 텍스트 노이즈 제거.

처리 순서:
1. ``fitz`` 로 PDF 를 페이지별로 열어 (a) 텍스트 블록(좌표 포함) + (b) 이미지(좌표·크기·바이너리)
   를 수집한다.
2. 규칙 기반 노이즈 제거:
   - **머리말/꼬리말**: 전체 페이지의 일정 비율 이상에 똑같이 등장하는 줄.
   - **페이지 번호**: ``1``, ``- 1 -``, ``Page 3``, ``1/10`` 등 흔한 패턴.
   - **목차**: 문서 앞쪽 N 페이지에서 ``...........5`` 처럼 점선 + 페이지번호 줄.
   - **연락처**: 이메일·전화번호 정규식.
   - **공백 정리**: 줄 안의 중복 공백, 빈 줄 정리.

자기소개·기술스택·프로젝트 설명·트러블슈팅 서술 등은 형태가 제각각이라
"제거 대상"을 룰로만 식별한다. 즉 룰에 안 걸리면 자동 보존.

이미지는 이 단계에서는 추출만 한다(트리아지·구조화는 다음 단계).
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter

import fitz

from app.core.common.interview_qa.dto import (
    ImageBlock,
    ParsedPortfolio,
    PdfPage,
    TextBlock,
)

logger = logging.getLogger(__name__)


# ---------------- 정규식 (코드 상수) ----------------
# 명확한 형태만 잡도록 보수적으로 작성. 본문 일부를 잘못 지우는 것보다
# 노이즈가 일부 남는 쪽이 안전하다.

# 페이지 번호 추정 패턴들. 한 줄이 이 중 하나에 매칭되면 노이즈로 본다.
_PAGE_NUMBER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*\d{1,4}\s*$"),  # "  3  "
    re.compile(r"^\s*-\s*\d{1,4}\s*-\s*$"),  # "- 3 -"
    re.compile(r"^\s*page\s+\d{1,4}\s*$", re.IGNORECASE),  # "Page 3"
    re.compile(r"^\s*\d{1,4}\s*/\s*\d{1,4}\s*$"),  # "3 / 10"
)

# 목차 줄 추정 — 점이 3개 이상 연속되고 끝에 페이지 번호가 붙은 형태.
# 예: "프로젝트 개요 .......... 5"
_TOC_LINE_PATTERN = re.compile(r"\.{3,}\s*\d{1,4}\s*$")

# 이메일 — 라인 안 어디에 있든 매칭되는 부분을 빈 문자열로 치환.
_EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

# 한국 휴대전화 / 일반전화 패턴. 너무 광범위하게 잡으면 본문 숫자가 사라지므로
# 하이픈으로 구분된 흔한 포맷에 한정한다.
_PHONE_PATTERN = re.compile(r"(?:\+?\d{1,3}[-\s])?\d{2,3}[-\s]\d{3,4}[-\s]\d{4}")

# 줄 안의 중복 공백을 하나로 줄이는 패턴.
_MULTI_SPACE_PATTERN = re.compile(r"[ \t]{2,}")


class Stage2PdfExtract:
    """PDF → ``ParsedPortfolio`` 변환 서비스."""

    def __init__(self, header_footer_min_ratio: float, toc_front_pages: int) -> None:
        # 노이즈 임계값은 OutboundProvider 가 Settings 에서 꺼내 평문으로 전달.
        # 작은 PDF 에서는 min_ratio 가 너무 낮으면 본문이 지워질 수 있으므로 운영 중 튜닝.
        self._header_footer_min_ratio = header_footer_min_ratio
        self._toc_front_pages = toc_front_pages

    async def execute(self, pdf_bytes: bytes) -> ParsedPortfolio:
        """PDF 바이트를 받아 ``ParsedPortfolio`` 를 돌려준다.

        fitz 호출은 CPU-bound 작업이므로 ``asyncio.to_thread`` 로 워커 스레드에서 실행한다.
        그래야 이벤트 루프가 막히지 않는다.
        """
        result: ParsedPortfolio = await asyncio.to_thread(self._extract_sync, pdf_bytes)

        # 종료 로그 — INFO 에는 메타만, DEBUG 에는 페이지별 텍스트 길이 등.
        total_text_chars = sum(len(b.text) for p in result.pages for b in p.text_blocks)
        total_images = sum(len(p.image_blocks) for p in result.pages)
        logger.info(
            "stage2_pdf_extract.done",
            extra={
                "page_count": len(result.pages),
                "total_text_chars": total_text_chars,
                "total_images": total_images,
            },
        )
        logger.debug(
            "stage2_pdf_extract.payload",
            extra={
                "pages": [
                    {
                        "page_number": p.page_number,
                        "text_block_count": len(p.text_blocks),
                        "image_count": len(p.image_blocks),
                    }
                    for p in result.pages
                ],
            },
        )
        return result

    # ---------------- 동기 본체 (워커 스레드에서 실행) ----------------

    def _extract_sync(self, pdf_bytes: bytes) -> ParsedPortfolio:
        """fitz 로 PDF 를 열고 텍스트·이미지를 수집한 뒤 노이즈 제거를 적용한다."""
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            # (1) 페이지별 raw 추출.
            raw_pages = [self._extract_page(doc, page_index) for page_index in range(doc.page_count)]

        # (2) 노이즈 제거를 raw 페이지 전체에 대해 적용.
        cleaned_pages = self._remove_noise(raw_pages)
        return ParsedPortfolio(pages=cleaned_pages)

    def _extract_page(self, doc: fitz.Document, page_index: int) -> PdfPage:
        """한 페이지에서 텍스트 블록과 이미지를 모은다."""
        page = doc[page_index]
        page_rect = page.rect

        # 텍스트 블록 — get_text("blocks") 가 (x0, y0, x1, y1, text, block_no, block_type) 을 돌려준다.
        # block_type == 0 이 텍스트 블록, 1 이 이미지 블록(좌표만 있고 바이너리는 따로 추출).
        text_blocks: list[TextBlock] = []
        for x0, y0, x1, y1, text, _block_no, block_type in page.get_text("blocks"):
            if block_type != 0:
                continue
            stripped = text.strip()
            if not stripped:
                continue
            text_blocks.append(TextBlock(text=stripped, bbox=(x0, y0, x1, y1)))

        # 이미지 — get_images 는 xref(이미지 참조 번호) 를 돌려주고, extract_image 로 바이너리,
        # get_image_rects 로 페이지 내 좌표를 얻는다.
        image_blocks: list[ImageBlock] = []
        for img_info in page.get_images(full=True):
            xref = int(img_info[0])
            extracted = doc.extract_image(xref)
            if not extracted:
                continue
            # 한 이미지가 페이지에 여러 번 그려질 수 있다. 첫 번째 좌표만 사용한다.
            rects = page.get_image_rects(xref)
            if not rects:
                continue
            rect = rects[0]
            image_blocks.append(
                ImageBlock(
                    image_bytes=extracted["image"],
                    width=int(extracted.get("width", 0)),
                    height=int(extracted.get("height", 0)),
                    bbox=(rect.x0, rect.y0, rect.x1, rect.y1),
                    xref=xref,
                )
            )

        return PdfPage(
            page_number=page_index,
            page_width=float(page_rect.width),
            page_height=float(page_rect.height),
            text_blocks=text_blocks,
            image_blocks=image_blocks,
        )

    # ---------------- 노이즈 제거 ----------------

    def _remove_noise(self, pages: list[PdfPage]) -> list[PdfPage]:
        """전체 페이지를 보고 머리/꼬리/페이지번호/목차/연락처/공백을 정리한다."""
        if not pages:
            return pages

        # 머리/꼬리 후보: 전체 페이지 중 일정 비율 이상에 동일하게 등장하는 줄.
        repeated_lines = self._find_repeated_lines(pages)

        cleaned: list[PdfPage] = []
        for page in pages:
            cleaned_blocks: list[TextBlock] = []
            for block in page.text_blocks:
                cleaned_text = self._clean_block_text(
                    block.text,
                    page_index=page.page_number,
                    repeated_lines=repeated_lines,
                )
                if cleaned_text:
                    cleaned_blocks.append(TextBlock(text=cleaned_text, bbox=block.bbox))
            cleaned.append(
                PdfPage(
                    page_number=page.page_number,
                    page_width=page.page_width,
                    page_height=page.page_height,
                    text_blocks=cleaned_blocks,
                    image_blocks=page.image_blocks,
                )
            )
        return cleaned

    def _find_repeated_lines(self, pages: list[PdfPage]) -> set[str]:
        """전체 페이지의 ``HEADER_FOOTER_MIN_RATIO`` 이상에 등장하는 줄들을 모은다.

        같은 줄을 한 페이지에서 여러 번 세는 일이 없도록, 페이지 단위로 set 을 만들고
        그 set 들을 합쳐서 카운트한다.
        """
        page_count = len(pages)
        if page_count == 0:
            return set()

        page_line_sets: list[set[str]] = []
        for page in pages:
            lines: set[str] = set()
            for block in page.text_blocks:
                for raw_line in block.text.splitlines():
                    line = raw_line.strip()
                    if line:
                        lines.add(line)
            page_line_sets.append(lines)

        # 줄별로 등장한 페이지 수 카운트.
        counter: Counter[str] = Counter()
        for lines in page_line_sets:
            counter.update(lines)

        min_pages_needed = max(2, int(page_count * self._header_footer_min_ratio))
        return {line for line, count in counter.items() if count >= min_pages_needed}

    def _clean_block_text(
        self,
        block_text: str,
        page_index: int,
        repeated_lines: set[str],
    ) -> str:
        """블록 안의 줄들을 하나씩 검사해 노이즈 줄을 빼고 나머지를 다시 합친다."""
        kept_lines: list[str] = []
        for raw_line in block_text.splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue

            # 머리/꼬리: 다른 페이지에도 동일하게 등장하는 줄.
            if stripped in repeated_lines:
                continue

            # 페이지 번호 패턴.
            if any(p.match(stripped) for p in _PAGE_NUMBER_PATTERNS):
                continue

            # 목차 줄 — 문서 앞쪽 일부 페이지에서만 검사.
            if page_index < self._toc_front_pages and _TOC_LINE_PATTERN.search(stripped):
                continue

            # 이메일·전화는 줄 자체를 지우지 않고 매칭된 부분만 비운다.
            line = _EMAIL_PATTERN.sub("", stripped)
            line = _PHONE_PATTERN.sub("", line)

            # 중복 공백 정리.
            line = _MULTI_SPACE_PATTERN.sub(" ", line).strip()
            if line:
                kept_lines.append(line)

        return "\n".join(kept_lines)
