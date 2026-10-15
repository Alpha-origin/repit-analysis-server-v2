from __future__ import annotations

from typing import Literal
from urllib.parse import quote

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

    @property
    def prompt_id(self) -> str:
        # 경계 태그와 충돌하지 않는 LLM용 식별자. 콜백에는 원래 fact_id를 돌려준다.
        return quote(self.fact_id, safe="")


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
