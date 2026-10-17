from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field

from app.core.common.dto import CamelModel

SourceKind = Literal["DOCUMENT", "VISION", "CODE", "SUPPLEMENT"]


class EvidenceV2(CamelModel):
    path: str
    quote: str = ""
    note: str = ""


class MaterialV2(CamelModel):
    category: str
    title: str
    summary: str
    evidence: list[EvidenceV2]


class MaterialOut(MaterialV2):
    source_kind: SourceKind


class ClaimCheckV2(CamelModel):
    claim: str
    reason: str
    evidence: list[EvidenceV2] = Field(default_factory=list)


class RepositoryV2(CamelModel):
    repo: str
    role: str
    description: str
    tech_stack: list[str] = Field(default_factory=list)


class SourceV2(CamelModel):
    id: str
    kind: Literal["DOCUMENT", "SUPPLEMENT", "REPOSITORY"]
    type: str | None = None
    title: str | None = None
    pages: int | None = None
    page_kinds: dict[str, int] = Field(default_factory=dict)
    unread_pages: list[int] = Field(default_factory=list)
    dropped_pages: list[int] = Field(default_factory=list)


class DraftProfileV2(CamelModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    overview: str = Field(min_length=1)
    materials: list[MaterialV2]
    claim_checks: list[ClaimCheckV2]
    repositories: list[RepositoryV2]


class ApplicantProfileV2(CamelModel):
    schema_version: Literal[2] = 2
    job_role: str
    overview: str
    sources: list[SourceV2]
    materials: list[MaterialOut]
    claim_checks: list[ClaimCheckV2] = Field(default_factory=list)
    repositories: list[RepositoryV2] = Field(default_factory=list)
