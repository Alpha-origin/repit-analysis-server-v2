from __future__ import annotations

from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

from app.core.common.company.dto import (
    BRIEF_MAX,
    FACT_CONTENT_MAX,
    FACT_TITLE_MAX,
    MAX_FACTS,
    SIGNAL_MAX,
    SIGNALS_MAX,
    CompanyContext,
    CompanyFact,
    CompanyFactKind,
)
from app.core.common.dto import CamelModel

Signal = Annotated[str, StringConstraints(min_length=1, max_length=SIGNAL_MAX)]


class CompanyFactRequest(CamelModel):
    fact_id: str = Field(..., min_length=1, max_length=32)
    kind: CompanyFactKind
    title: str = Field(..., min_length=1, max_length=FACT_TITLE_MAX)
    content: str = Field(..., min_length=1, max_length=FACT_CONTENT_MAX)
    positive_signals: list[Signal] = Field(default_factory=list, max_length=SIGNALS_MAX)
    concern_signals: list[Signal] = Field(default_factory=list, max_length=SIGNALS_MAX)


class CompanyContextRequest(CamelModel):
    company_id: str = Field(..., min_length=1)
    display_name: str = Field(..., min_length=1, max_length=30)
    category: str = Field(..., min_length=1)
    facts: list[CompanyFactRequest] = Field(..., min_length=1, max_length=MAX_FACTS)
    interviewer_brief: str = Field(..., min_length=1, max_length=BRIEF_MAX)

    @model_validator(mode="after")
    def _check(self) -> CompanyContextRequest:
        ids = [fact.fact_id for fact in self.facts]
        if len(set(ids)) != len(ids):
            raise ValueError("factId 는 기업 안에서 유일해야 합니다.")
        kinds = {fact.kind for fact in self.facts}
        if not {"TALENT", "BUSINESS"} <= kinds:
            raise ValueError("TALENT 와 BUSINESS 정보가 각각 1개 이상 필요합니다.")
        if any(fact.kind != "TALENT" and (fact.positive_signals or fact.concern_signals) for fact in self.facts):
            raise ValueError("긍정·우려 신호는 TALENT 에만 넣을 수 있습니다.")
        return self

    def to_core(self) -> CompanyContext:
        return CompanyContext(
            company_id=self.company_id,
            display_name=self.display_name,
            category=self.category,
            facts=tuple(
                CompanyFact(
                    fact_id=fact.fact_id,
                    kind=fact.kind,
                    title=fact.title,
                    content=fact.content,
                    positive_signals=tuple(fact.positive_signals),
                    concern_signals=tuple(fact.concern_signals),
                )
                for fact in self.facts
            ),
            interviewer_brief=self.interviewer_brief,
        )
