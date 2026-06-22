"""Stage 2-5 — text_heavy / image_heavy 두 분기를 합쳐 단일 통합 문서를 만든다.

규칙:
- 페이지 순서대로 텍스트 블록을 이어 붙인다.
- ``image_heavy`` 분기일 때 한해 각 페이지의 텍스트 직후에 그 페이지의 ``ImageStructure``
  들을 정해진 포맷으로 끼워 넣는다(`source_page` 기준).
- ``text_heavy`` 분기는 이미지 블록 자체가 없는 입력이라 텍스트만 합쳐 나온다.

이 단계 이후로는 원본 이미지를 다시 보지 않는다(다음 단계 LLM 탐색의 입력은
오직 ``portfolio_text``).
"""

from __future__ import annotations

import logging

from app.core.common.interview_qa.dto import (
    ImageStructure,
    MergedDocument,
    StructuredPortfolio,
)

logger = logging.getLogger(__name__)


class Stage2DocumentMerge:
    """순수 함수에 가까운 동기 합치기 작업을 단계 서비스 시그니처에 맞춰 async 로 노출."""

    async def execute(self, structured: StructuredPortfolio) -> MergedDocument:
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
        logger.debug("stage2_document_merge.payload", extra={"portfolio_text": portfolio_text})
        return MergedDocument(portfolio_text=portfolio_text, branch=structured.branch)

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
            if page_text:
                sections.append(page_text)

            # 같은 페이지 번호로 들어온 이미지 블록을 텍스트 바로 다음에 끼워 넣는다.
            sections.extend(self._format_image(img) for img in images_by_page.get(page.page_number, []))

        # 페이지/이미지 블록은 빈 줄(\n\n) 으로 구분해 사람과 LLM 둘 다 읽기 좋게.
        return "\n\n".join(sections)

    @staticmethod
    def _format_image(img: ImageStructure) -> str:
        """이미지 블록을 본문에 끼워 넣는 한국어 포맷.

        예시:
        ::

            [이미지: architecture_diagram, p.4]
            API Gateway 뒤에 주문/결제/재고 서비스가 분리된 MSA 구조, Kafka 사용
            기술 신호: MSA, Kafka, API Gateway
        """
        lines = [f"[이미지: {img.image_type}, p.{img.source_page}]"]
        if img.summary.strip():
            lines.append(img.summary.strip())
        if img.tech_signals:
            # 빈 문자열 키워드는 제거하고 보기 좋게 콤마로 묶는다.
            signals = ", ".join(s for s in img.tech_signals if s.strip())
            if signals:
                lines.append(f"기술 신호: {signals}")
        return "\n".join(lines)
