from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

import fitz

from app.core.common.interview_qa.dto import PdfPage, TextBlock
from app.core.common.interview_qa.errors import PipelineError
from app.core.common.interview_qa.stage2_nondev.classify import DEFAULT_POLICY, PagePolicy, classify_page, plan_regions
from app.core.common.interview_qa.stage2_nondev.dto import (
    BBox,
    EvidenceBlock,
    ExtractedDocument,
    ExtractedPage,
    ProfileDocument,
    TableData,
)
from app.core.common.interview_qa.stage2_nondev.masking import mask_text
from app.core.common.interview_qa.stage2_nondev.tables import (
    combine_continued_tables,
    mark_continuations,
    markdown_table,
)
from app.core.common.interview_qa.stage2_pdf_extract import Stage2PdfExtract

MAX_PAGES = 50
LOGO_AREA = 0.05
MIN_LOGO_REPETITIONS = 2


def _bbox(value: Any) -> BBox:
    return float(value[0]), float(value[1]), float(value[2]), float(value[3])


def _inside(box: BBox, container: BBox) -> bool:
    return box[0] >= container[0] and box[1] >= container[1] and box[2] <= container[2] and box[3] <= container[3]


class DocumentExtract:
    def __init__(self, cleaner: Stage2PdfExtract, policy: PagePolicy = DEFAULT_POLICY) -> None:
        self.cleaner = cleaner
        self.policy = policy

    async def execute(self, inputs: list[tuple[ProfileDocument, bytes]]) -> list[ExtractedDocument]:
        return await asyncio.to_thread(self._extract_all, inputs)

    def _extract_all(self, inputs: list[tuple[ProfileDocument, bytes]]) -> list[ExtractedDocument]:
        documents = []
        page_count = 0
        for index, (source, data) in enumerate(inputs, 1):
            try:
                with fitz.open(stream=data, filetype="pdf") as pdf:
                    if pdf.needs_pass or pdf.page_count < 1:
                        raise ValueError("locked or empty PDF")
                    page_count += pdf.page_count
                    if page_count > MAX_PAGES:
                        raise PipelineError(422, "PDF 합계는 50쪽까지 분석할 수 있습니다.")
                    pages = self._extract_pages(pdf, f"doc-{index}")
            except PipelineError:
                raise
            except Exception as exc:
                raise PipelineError(422, f"doc-{index}: 열 수 없는 PDF이거나 암호화된 문서입니다.") from exc
            mark_continuations(pages)
            documents.append(
                ExtractedDocument(
                    f"doc-{index}", source.type, source.title, data, pages, combine_continued_tables(pages)
                )
            )
        return documents

    def _extract_pages(self, pdf: Any, doc_id: str) -> list[ExtractedPage]:
        infos = [page.get_image_info(hashes=True) for page in pdf]
        repeats = Counter(digest for page_infos in infos for digest in {info.get("digest") for info in page_infos})
        raw_pages = []
        for number, page in enumerate(pdf):
            blocks = [
                TextBlock(text=mask_text(str(raw[4])), bbox=_bbox(raw))
                for raw in page.get_text("blocks")
                if raw[6] == 0 and str(raw[4]).strip()
            ]
            raw_pages.append(
                PdfPage(
                    page_number=number,
                    page_width=page.rect.width,
                    page_height=page.rect.height,
                    text_blocks=blocks,
                    image_blocks=[],
                )
            )
        # Reuse the existing header/footer/page-number/TOC rules; masking has already preserved PII markers.
        margins = [
            page.model_copy(
                update={
                    "text_blocks": [
                        block
                        for block in page.text_blocks
                        if block.bbox[1] <= page.page_height * 0.1 or block.bbox[3] >= page.page_height * 0.9
                    ]
                }
            )
            for page in raw_pages
        ]
        repeated = self.cleaner._find_repeated_lines(margins)
        cleaned = [
            page.model_copy(
                update={
                    "text_blocks": [
                        block.model_copy(update={"text": text})
                        for block in page.text_blocks
                        if (text := self.cleaner._clean_block_text(block.text, page.page_number, repeated))
                    ]
                }
            )
            for page in raw_pages
        ]
        return [self._page(page, doc_id, cleaned[index], infos[index], repeats) for index, page in enumerate(pdf)]

    def _page(
        self,
        page: Any,
        doc_id: str,
        cleaned: PdfPage,
        image_infos: list[dict[str, Any]],
        repeats: Counter[Any],
    ) -> ExtractedPage:
        number = cleaned.page_number + 1
        path = f"{doc_id}/p{number}"
        bounds = _bbox(page.rect)
        area = max(1.0, page.rect.width * page.rect.height)
        images = [info for info in image_infos if not _repeated_margin_logo(info, bounds, area, repeats)]
        image_area = min(1.0, sum(fitz.Rect(info["bbox"]).get_area() for info in images) / area)
        drawings = page.get_drawings()
        tables, table_error = _tables(page, path, cleaned)
        raw_text = page.get_text()
        kind = classify_page(
            raw_text, image_area, len(drawings), has_visual=bool(images or drawings or table_error), policy=self.policy
        )
        blocks = [
            EvidenceBlock(f"{path}/text-{index}", path, block.bbox, "TEXT", block.text)
            for index, block in enumerate(cleaned.text_blocks, 1)
            if not any(_inside(block.bbox, table.bbox) for table in tables)
        ]
        blocks.extend(
            EvidenceBlock(f"{path}/table-{index}", path, table.bbox, "TABLE", markdown_table(table))
            for index, table in enumerate(tables, 1)
        )
        dense_table = any(
            len(table.rows) > 40 or sum(len(cell) for row in table.rows for cell in row) >= 4000  # noqa: PLR2004
            for table in tables
        )
        regions, unread = (
            plan_regions(number, bounds, self.policy, dense_table=dense_table)
            if kind in {"B", "D", "E", "F"}
            else ([], False)
        )
        if kind == "F":
            blocks = [block for block in blocks if block.method == "TABLE"]
        return ExtractedPage(number, kind, bounds, blocks, tables, regions, unread)


def _repeated_margin_logo(info: dict[str, Any], bounds: BBox, area: float, repeats: Counter[Any]) -> bool:
    box = _bbox(info["bbox"])
    digest = info.get("digest")
    in_margin = box[3] <= bounds[3] * 0.15 or box[1] >= bounds[3] * 0.85
    return (
        digest is not None
        and repeats[digest] >= MIN_LOGO_REPETITIONS
        and in_margin
        and fitz.Rect(box).get_area() / area < LOGO_AREA
    )


def _tables(page: Any, path: str, cleaned: PdfPage) -> tuple[list[TableData], bool]:
    result = []
    try:
        found = page.find_tables()
        for table in found.tables:
            rows = tuple(tuple(mask_text(str(cell or "")) for cell in row) for row in table.extract())
            box = _bbox(table.bbox)
            above = [block for block in cleaned.text_blocks if 0 <= box[1] - block.bbox[3] < 40]  # noqa: PLR2004
            title = max(above, key=lambda block: block.bbox[3]).text if above else ""
            result.append(TableData(path, box, rows, tuple([cleaned.page_number + 1] * len(rows)), title))
    except Exception:
        return [], True
    return result, False
