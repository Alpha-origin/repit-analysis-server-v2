from __future__ import annotations

import logging

from app.core.common.interview_qa.dto import (
    ImageStructure,
    MergedDocument,
    StructuredPortfolio,
)
from app.core.common.interview_qa.pdf_document_report import document_report, require_document_content

logger = logging.getLogger(__name__)


class Stage2DocumentMerge:
    async def execute(self, structured: StructuredPortfolio) -> MergedDocument:
        report = document_report(structured)
        require_document_content(structured, report)
        portfolio_text = self._merge(structured)

        # 종료 로그 — INFO 메타 / DEBUG 본문 전체.
        logger.info(
            "stage2_document_merge.done",
            extra={
                "branch": structured.branch,
                "text_chars": len(portfolio_text),
                "inserted_images": len(structured.structured_images),
                "page_count": len(structured.pages),
            },
        )
        return MergedDocument(portfolio_text=portfolio_text, branch=structured.branch, document_extraction=report)

    # ---------------- 내부 ----------------

    def _merge(self, structured: StructuredPortfolio) -> str:
        # 페이지 번호 → 그 페이지의 ImageStructure 들. structured_images 내부 순서를 보존.
        images_by_page: dict[int, list[ImageStructure]] = {}
        for img in structured.structured_images:
            images_by_page.setdefault(img.source_page, []).append(img)

        sections: list[str] = []
        for page in structured.pages:
            # 페이지 텍스트 — 블록 사이에 빈 줄을 끼워 가독성 확보.
            page_text = "\n\n".join(b.text for b in page.text_blocks if b.text.strip())
            if page.text_recovery.evaluated:
                sections.append(
                    f"[PDF p.{page.page_number + 1} source={page.text_source} status={page.text_recovery.state}]"
                )
                if page.text_recovery.unreadable_regions or page.text_recovery.state in {
                    "partial",
                    "failed",
                    "skipped",
                }:
                    sections.append("[미판독 또는 충돌 영역 존재: 누락 내용을 추정하거나 확정 근거로 사용하지 말 것]")
            if page_text:
                sections.append(page_text)

            # 같은 페이지 번호로 들어온 이미지 블록을 텍스트 바로 다음에 끼워 넣는다.
            for img in images_by_page.get(page.page_number, []):
                # A verbatim transcription already present in page text adds no semantic information.
                if img.summary.strip() and img.summary.strip() == page_text.strip() and not img.tech_signals:
                    continue
                sections.append(self._format_image(img))

        # 페이지/이미지 블록은 빈 줄(\n\n) 으로 구분해 사람과 LLM 둘 다 읽기 좋게.
        return "\n\n".join(sections)

    @staticmethod
    def _format_image(img: ImageStructure) -> str:
        lines = [f"[이미지: {img.image_type}, p.{img.source_page + 1}]"]
        if img.summary.strip():
            lines.append(img.summary.strip())
        if img.tech_signals:
            # 빈 문자열 키워드는 제거하고 보기 좋게 콤마로 묶는다.
            signals = ", ".join(s for s in img.tech_signals if s.strip())
            if signals:
                lines.append(f"기술 신호: {signals}")
        return "\n".join(lines)
