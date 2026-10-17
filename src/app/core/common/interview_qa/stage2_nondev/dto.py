from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.common.dto import CamelModel

BBox = tuple[float, float, float, float]
PageKind = Literal["A", "B", "C", "D", "E", "F"]
Extraction = Literal["TEXT", "TABLE", "VISION_TRANSCRIPT", "VISION_RELATION"]


class ProfileDocument(CamelModel):
    type: str
    url: str
    title: str | None = None


class ProfileSupplement(CamelModel):
    title: str
    document_index: int | None = None
    goal: str = ""
    role: str = ""
    decision: str = ""
    outcome: str = ""


@dataclass(frozen=True)
class EvidenceBlock:
    id: str
    path: str
    bbox: BBox
    method: Extraction
    text: str


@dataclass(frozen=True)
class TableData:
    path: str
    bbox: BBox
    rows: tuple[tuple[str, ...], ...]
    row_pages: tuple[int, ...]
    title: str = ""
    continued_from: str | None = None


@dataclass(frozen=True)
class ReadRegion:
    page: int
    bbox: BBox
    tiled: bool = False


@dataclass
class ExtractedPage:
    number: int
    kind: PageKind
    bbox: BBox
    blocks: list[EvidenceBlock]
    tables: list[TableData]
    regions: list[ReadRegion]
    unread: bool = False


@dataclass
class ExtractedDocument:
    id: str
    type: str
    title: str | None
    pdf: bytes
    pages: list[ExtractedPage]
    linked_tables: list[TableData] = field(default_factory=list)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ReadingRelation(StrictModel):
    from_: str = Field(alias="from")
    to: str
    label: str


class ReadingChart(StrictModel):
    metric: str
    axes: str
    trend: str
    values: list[str]


class PageReading(StrictModel):
    kind: Literal["text", "table", "diagram", "chart", "screenshot", "photo", "decorative"]
    transcript: str
    relations: list[ReadingRelation]
    chart: ReadingChart
    summary: str
    legibility: Literal["good", "partial", "low"]


@dataclass
class ReadingUsage:
    initial_calls: int = 0
    reread_calls: int = 0
    sdk_retries: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    refusals: int = 0
    elapsed_seconds: float = 0


@dataclass
class DocumentReading:
    documents: list[ExtractedDocument]
    summaries: dict[str, list[str]] = field(default_factory=dict)
    usage: ReadingUsage = field(default_factory=ReadingUsage)

    @property
    def blocks(self) -> list[EvidenceBlock]:
        return [
            block
            for document in self.documents
            for page in document.pages
            for block in page.blocks
            if not (page.kind == "E" and block.method == "TEXT")
        ]
