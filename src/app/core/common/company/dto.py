from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

CompanyFactKind = Literal["TALENT", "BUSINESS", "MISSION_VISION", "PROBLEM", "TECH_STACK"]

MAX_FACTS = 20
FACT_TITLE_MAX = 20
FACT_CONTENT_MAX = 200
SIGNALS_MAX = 3
SIGNAL_MAX = 100
BRIEF_MAX = 600


class CompanyFact(BaseModel):
    model_config = ConfigDict(frozen=True)

    fact_id: str
    kind: CompanyFactKind
    title: str
    content: str
    positive_signals: tuple[str, ...] = ()
    concern_signals: tuple[str, ...] = ()


class CompanyContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    company_id: str
    display_name: str
    category: str
    facts: tuple[CompanyFact, ...]
    interviewer_brief: str

    def facts_of(self, *kinds: CompanyFactKind) -> tuple[CompanyFact, ...]:
        return tuple(fact for fact in self.facts if fact.kind in kinds)

    def fact_by_id(self, fact_id: str) -> CompanyFact | None:
        return next((fact for fact in self.facts if fact.fact_id == fact_id), None)
