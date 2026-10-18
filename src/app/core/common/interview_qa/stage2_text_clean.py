from __future__ import annotations

import re
from collections import Counter

from app.core.common.interview_qa.dto import ParsedPortfolio, PdfPage, TextBlock

# ---------------- 정규식 (코드 상수) ----------------
# 명확한 형태만 잡도록 보수적으로 작성. 본문 일부를 잘못 지우는 것보다
# 노이즈가 일부 남는 쪽이 안전하다.

# 페이지 번호 추정 패턴들. 한 줄이 이 중 하나에 매칭되면 노이즈로 본다.
_CLEANUP_MIN_CHARS = 50

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


class Stage2TextClean:
    def __init__(self, header_footer_min_ratio: float, toc_front_pages: int) -> None:
        self._header_footer_min_ratio = header_footer_min_ratio
        self._toc_front_pages = toc_front_pages

    async def execute(self, portfolio: ParsedPortfolio) -> ParsedPortfolio:
        return portfolio.model_copy(update={"pages": self._remove_noise(portfolio.pages)})

    def _remove_noise(self, pages: list[PdfPage]) -> list[PdfPage]:
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
                    cleaned_blocks.append(block.model_copy(update={"text": cleaned_text}))
            recovery = page.text_recovery.model_copy(deep=True)
            before = sum(len(block.text) for block in page.text_blocks)
            after = sum(len(block.text) for block in cleaned_blocks)
            if before >= _CLEANUP_MIN_CHARS and after < before * 0.5:
                recovery.diagnostics.append("cleanup_suspect")
            cleaned.append(page.model_copy(update={"text_blocks": cleaned_blocks, "text_recovery": recovery}))
        return cleaned

    def _find_repeated_lines(self, pages: list[PdfPage]) -> set[str]:
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
