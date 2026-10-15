from __future__ import annotations

from pydantic import Field

from app.core.common.company.dto import CompanyFactKind
from app.core.common.dto import CamelModel

FIT_COMMENT_MAX = 300
FIT_ITEM_TEXT_MAX = 150
FIT_ITEMS_MAX = 3


class CompanyFitItem(CamelModel):
    fact_id: str
    kind: CompanyFactKind
    focus: str
    evidence: str = Field(..., min_length=1, max_length=FIT_ITEM_TEXT_MAX)
    question_ids: list[str]


class CompanyFitGap(CamelModel):
    fact_id: str
    kind: CompanyFactKind
    focus: str
    suggestion: str = Field(..., min_length=1, max_length=FIT_ITEM_TEXT_MAX)
    question_ids: list[str]


class CompanyFitPayload(CamelModel):
    comment: str = Field(..., min_length=1, max_length=FIT_COMMENT_MAX)
    matched_focus: list[CompanyFitItem] = Field(..., max_length=FIT_ITEMS_MAX)
    gaps: list[CompanyFitGap] = Field(..., max_length=FIT_ITEMS_MAX)
