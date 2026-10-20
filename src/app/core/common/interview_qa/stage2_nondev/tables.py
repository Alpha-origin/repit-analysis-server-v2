from __future__ import annotations

from dataclasses import replace
from itertools import pairwise

from app.core.common.interview_qa.stage2_nondev.dto import ExtractedPage, TableData
from app.core.common.interview_qa.stage2_nondev.masking import normalize_quote

FIRST_ROWS = 20


def select_rows(total: int, cap: int | None) -> tuple[int, ...]:
    if cap is None or total <= cap:
        return tuple(range(total))
    first = min(FIRST_ROWS, cap)
    remaining = cap - first
    if not remaining:
        return tuple(range(first))
    sampled = tuple(first + round(i * (total - first - 1) / max(1, remaining - 1)) for i in range(remaining))
    return tuple(range(first)) + sampled


def markdown_table(table: TableData, cap: int | None = None) -> str:
    selected = select_rows(len(table.rows), cap)
    lines = []
    if len(selected) != len(table.rows):
        lines.append(
            f"(전체 {len(table.rows)}행 중 {len(selected)}행, 원본 행 번호: {','.join(str(i + 1) for i in selected)})"
        )
    for index in selected:
        lines.append(
            "| " + " | ".join(cell.replace("|", "\\|").replace("\n", " ") for cell in table.rows[index]) + " |"
        )
        if index == 0:
            lines.append("| " + " | ".join("---" for _ in table.rows[0]) + " |")
    return "\n".join(lines)


def mark_continuations(pages: list[ExtractedPage]) -> None:
    for previous, current in pairwise(pages):
        if current.number != previous.number + 1 or not previous.tables or not current.tables:
            continue
        left, right = previous.tables[-1], current.tables[0]
        if not left.rows or not right.rows or not all(left.rows[0]):
            continue
        same_header = tuple(map(normalize_quote, left.rows[0])) == tuple(map(normalize_quote, right.rows[0]))
        bottom = left.bbox[3] >= previous.bbox[3] * 0.85
        top = right.bbox[1] <= current.bbox[3] * 0.15
        conflicting_title = bool(right.title and normalize_quote(right.title) != normalize_quote(left.title))
        if same_header and bottom and top and not conflicting_title:
            current.tables[0] = replace(right, continued_from=left.path)


def combine_continued_tables(pages: list[ExtractedPage]) -> list[TableData]:
    result: list[TableData] = []
    previous_path: str | None = None
    for page in pages:
        for table in page.tables:
            if result and table.continued_from is not None and table.continued_from == previous_path:
                previous = result[-1]
                result[-1] = replace(
                    previous, rows=previous.rows + table.rows[1:], row_pages=previous.row_pages + table.row_pages[1:]
                )
            else:
                result.append(table)
            previous_path = table.path
    return result
