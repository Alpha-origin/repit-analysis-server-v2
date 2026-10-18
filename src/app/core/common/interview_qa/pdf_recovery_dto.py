from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.common.dto import CamelModel

NORMALIZED_MAX = 1000
BOX_LENGTH = 4

Region = tuple[float, float, float, float]
RecoveryState = Literal["not_required", "recovered", "partial", "no_text", "failed", "skipped"]
RecoveryFailure = Literal[
    "api_error",
    "timeout",
    "invalid_response",
    "truncated",
    "render_error",
    "skipped_budget",
    "feature_disabled",
]
ApiErrorKind = Literal["transient", "configuration", "unknown"]


class UncertainText(BaseModel):
    text: str
    bbox: Region
    source_region: Region


class TextRecovery(BaseModel):
    evaluated: bool = False
    state: RecoveryState = "not_required"
    reasons: list[str] = Field(default_factory=list)
    failure_reason: RecoveryFailure | None = None
    error_kind: ApiErrorKind | None = None
    unreadable_regions: list[Region] = Field(default_factory=list)
    uncertain_blocks: list[UncertainText] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
    call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class ExtractionWarning(CamelModel):
    page: int
    code: str


class DocumentExtractionReport(CamelModel):
    status: Literal["complete", "partial", "not_evaluated"] = "not_evaluated"
    page_count: int = 0
    recovered_pages: list[int] = Field(default_factory=list)
    partial_pages: list[int] = Field(default_factory=list)
    failed_pages: list[int] = Field(default_factory=list)
    skipped_pages: list[int] = Field(default_factory=list)
    no_text_pages: list[int] = Field(default_factory=list)
    warnings: list[ExtractionWarning] = Field(default_factory=list)


class RecoveryPlan(BaseModel):
    page_number: int
    reasons: list[str]
    priority: int


class RecoveryOptions(BaseModel):
    enabled: bool = False
    max_pages: int = Field(20, ge=1)
    max_calls: int = Field(40, ge=1)
    concurrency: int = Field(2, ge=1)
    max_tokens: int = Field(8192, ge=1)
    call_timeout_seconds: float = Field(30, gt=0)
    total_timeout_seconds: float = Field(120, gt=0)
    render_dpi: int = Field(150, ge=36)
    render_max_pixels: int = Field(12_000_000, ge=1024)
    max_tiles_per_page: int = Field(4, ge=0, le=4)
    vision_long_edge_px: int = Field(1500, ge=64)


class TranscribedBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["heading", "paragraph", "list", "table", "caption"]
    text: str = Field(min_length=1)
    bbox_normalized: list[float] = Field(min_length=4, max_length=4)
    legibility: Literal["clear", "uncertain"]

    @model_validator(mode="after")
    def validate_box(self) -> TranscribedBlock:
        validate_normalized_box(self.bbox_normalized)
        if not self.text.strip():
            raise ValueError("Blank transcription")
        return self


def validate_normalized_box(box: list[float]) -> None:
    if not all(0 <= value <= NORMALIZED_MAX for value in box) or box[0] >= box[2] or box[1] >= box[3]:
        raise ValueError("Invalid normalized region")


class PageTranscription(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["text_present", "no_text", "unreadable"]
    blocks: list[TranscribedBlock]
    unreadable_regions: list[list[float]]
    needs_zoom: bool

    @model_validator(mode="after")
    def validate_content(self) -> PageTranscription:
        for box in self.unreadable_regions:
            if len(box) != BOX_LENGTH:
                raise ValueError("Invalid unreadable region")
            validate_normalized_box(box)
        if self.status == "no_text" and (self.blocks or self.unreadable_regions or self.needs_zoom):
            raise ValueError("Inconsistent no-text response")
        if self.status == "text_present" and not self.blocks:
            raise ValueError("Missing text blocks")
        if self.status == "unreadable" and self.blocks:
            raise ValueError("Unreadable response must not claim text blocks")
        return self
