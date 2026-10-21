from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from app.core.common.dto import CamelModel
from app.core.common.feedback.competency.catalog import WEIGHTS, Competency

Verdict = Literal["STRONG", "SOLID", "BORDERLINE", "WEAK"]


class MotivationSummary(CamelModel):
    score: int | None = Field(..., ge=0, le=100)
    verdict: Verdict | None
    evidence_count: int = Field(..., ge=0)


class CompetencyAxisScore(MotivationSummary):
    axis: Competency
    weight: int | None = Field(..., ge=0, le=100)


class ResponseSummary(CamelModel):
    score: int = Field(..., ge=0, le=100)
    hit_count: int = Field(..., ge=0)
    graded_count: int = Field(..., ge=0)


class CredibilityIssue(CamelModel):
    kind: Literal["ROLE", "TECH_CHOICE", "CAUSE", "DETAIL", "OTHER"]
    question_ids: tuple[str, str]
    quotes: tuple[str, str]


class CredibilityPayload(CamelModel):
    score: int | None = Field(..., ge=0, le=100)
    issues: list[CredibilityIssue]


class CompetencyBreakdownPayload(CamelModel):
    scoring_version: Literal["competency-v1"]
    axes: list[CompetencyAxisScore] = Field(..., min_length=5, max_length=5)
    consistency_score: int | None = Field(..., ge=0, le=100)
    response: ResponseSummary
    motivation: MotivationSummary | None
    credibility: CredibilityPayload | None

    @model_validator(mode="after")
    def check_axes(self) -> CompetencyBreakdownPayload:
        if [axis.axis for axis in self.axes] != list(WEIGHTS):
            raise ValueError("역량 축의 목록과 순서가 다릅니다")
        for axis in self.axes:
            if axis.score is None and (axis.weight is not None or axis.verdict is not None or axis.evidence_count != 0):
                raise ValueError("미관측 역량은 점수·가중치·판정이 null이고 근거 수가 0이어야 합니다")
        return self


class EvidenceQuote(CamelModel):
    slot: str
    quote: str


class CompetencyItemPayload(CamelModel):
    competency: Competency
    role: Literal["PRIMARY", "SECONDARY"]
    level: int = Field(..., ge=0, le=4)
    score: int = Field(..., ge=0, le=100)
    evidence: list[EvidenceQuote]
