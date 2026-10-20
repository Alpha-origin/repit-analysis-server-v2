from __future__ import annotations

from collections import Counter
from dataclasses import replace

from app.core.common.applicant_profile.dto_v2 import SourceV2
from app.core.common.interview_qa.stage2_nondev.dto import (
    DocumentReading,
    EvidenceBlock,
    ExtractedDocument,
    ExtractedPage,
    ProfileCoverLetter,
    ProfileSupplement,
    TableData,
)
from app.core.common.interview_qa.stage2_nondev.masking import mask_text, normalize_quote
from app.core.common.interview_qa.stage2_nondev.tables import markdown_table
from app.core.common.job_spec import DOC_TYPES


def page_text(document: ExtractedDocument, page: ExtractedPage) -> str:
    lines = [
        f"=== [{document.id} · {DOC_TYPES[document.type].label} · {mask_text(document.title or '')}] ===",
        f"--- p.{page.number} ({page.kind}) ---",
    ]
    table_index = {f"{document.id}/p{page.number}/table-{index}": table for index, table in enumerate(page.tables, 1)}
    seen: set[str] = set()
    transcripts: list[EvidenceBlock] = []
    for block in page.blocks:
        normalized = normalize_quote(block.text)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        text = _model_table(table_index[block.id], document) if block.id in table_index else block.text
        if block.method == "VISION_TRANSCRIPT":
            for previous in transcripts:
                if _overlaps(previous, block):
                    text = _without_repeated_prefix(previous.text, text)
            transcripts.append(block)
        label = "참고 라벨 · 인용 불가" if page.kind == "E" and block.method == "TEXT" else block.method
        if text.strip():
            lines.append(f"[{label} · {block.id}]\n{text}")
    return "\n".join(lines)


def _overlaps(left: EvidenceBlock, right: EvidenceBlock) -> bool:
    return max(left.bbox[0], right.bbox[0]) < min(left.bbox[2], right.bbox[2]) and max(
        left.bbox[1], right.bbox[1]
    ) < min(left.bbox[3], right.bbox[3])


def _without_repeated_prefix(previous: str, text: str) -> str:
    # Only boundary lines in overlapping regions; repeated rows inside a block stay intact.
    left, right = previous.strip().splitlines(), text.strip().splitlines()
    for count in range(min(len(left), len(right)), 0, -1):
        suffix, prefix = normalize_quote("\n".join(left[-count:])), normalize_quote("\n".join(right[:count]))
        if len(prefix) >= 8 and suffix == prefix:  # noqa: PLR2004
            return "\n".join(right[count:])
    return text


def _model_table(table: TableData, document: ExtractedDocument) -> str:
    prefix = f"[이전 쪽 표에서 계속: {table.continued_from}]\n" if table.continued_from else ""
    if document.type == "TABLE_SPEC" and sum(len(page.tables) for page in document.pages) > 30 and table.rows:  # noqa: PLR2004
        headers = table.rows[0]
        selected = [
            index
            for index, label in enumerate(headers)
            if index == 0
            or any(key in label.lower() for key in ("키", "인덱스", "제약", "key", "index", "constraint", "pk", "fk"))
        ]
        table = replace(
            table, rows=tuple(tuple(row[index] for index in selected if index < len(row)) for row in table.rows)
        )
        prefix += f"(전체 {len(headers)}열 중 키·인덱스·제약 중심 {len(selected)}열, 원본은 근거 색인에 보존)\n"
    return prefix + markdown_table(table, DOC_TYPES[document.type].row_cap)


def supplement_blocks(supplements: tuple[ProfileSupplement, ...]) -> list[EvidenceBlock]:
    return [
        EvidenceBlock(f"supp-{index}/{field}", f"supp-{index}/{field}", (0, 0, 0, 0), "TEXT", mask_text(value))
        for index, supplement in enumerate(supplements, 1)
        for field in ("goal", "role", "decision", "outcome")
        if (value := getattr(supplement, field)).strip()
    ]


def format_supplements(supplements: tuple[ProfileSupplement, ...]) -> str:
    pieces = []
    for index, supplement in enumerate(supplements, 1):
        link = f" · 연결 문서 doc-{supplement.document_index + 1}" if supplement.document_index is not None else ""
        # 제목은 선택 항목이다. 비었으면 구분자째 뺀다.
        title = f" · {mask_text(supplement.title)}" if supplement.title.strip() else ""
        pieces.append(f"=== [supp-{index} · 보완 입력{title}{link}] ===")
        pieces.extend(
            f"[supp-{index}/{field}] {mask_text(value)}"
            for field in ("goal", "role", "decision", "outcome")
            if (value := getattr(supplement, field)).strip()
        )
    return "\n".join(pieces)


def sources(
    reading: DocumentReading,
    supplements: tuple[ProfileSupplement, ...],
    cover_letters: tuple[ProfileCoverLetter, ...] = (),
) -> list[SourceV2]:
    return (
        [
            SourceV2(
                id=document.id,
                kind="DOCUMENT",
                type=document.type,
                title=mask_text(document.title or ""),
                pages=len(document.pages),
                page_kinds=dict(Counter(page.kind for page in document.pages)),
                unread_pages=[page.number for page in document.pages if page.unread],
            )
            for document in reading.documents
        ]
        + [
            SourceV2(id=f"supp-{index}", kind="SUPPLEMENT", title=mask_text(supplement.title))
            for index, supplement in enumerate(supplements, 1)
        ]
        + [
            SourceV2(id=f"cl-{index}", kind="COVER_LETTER", title=mask_text(letter.question)[:80])
            for index, letter in enumerate(cover_letters, 1)
        ]
    )


def cover_letter_blocks(cover_letters: tuple[ProfileCoverLetter, ...]) -> list[EvidenceBlock]:
    return [
        EvidenceBlock(f"cl-{index}", f"cl-{index}", (0, 0, 0, 0), "TEXT", mask_text(letter.answer))
        for index, letter in enumerate(cover_letters, 1)
    ]


def format_cover_letters(cover_letters: tuple[ProfileCoverLetter, ...]) -> str:
    return "\n".join(
        f"=== [cl-{index} · 자소서 · 문항: {mask_text(letter.question)}] ===\n[cl-{index}] {mask_text(letter.answer)}"
        for index, letter in enumerate(cover_letters, 1)
    )
