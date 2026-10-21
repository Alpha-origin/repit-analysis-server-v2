from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.core.common.feedback.competency.catalog import Competency

TargetSource = Literal["job", "non_tech", "inferred", "unclassified"]


@dataclass(frozen=True)
class CompetencyTargets:
    primary: Competency | None
    secondary: tuple[Competency, ...] = ()
    source: TargetSource = "unclassified"


PS, KN, OW, COL, GR, MOT = tuple(Competency)
TargetTable = dict[str, tuple[Competency, tuple[Competency, ...]]]

DEV_TABLE: TargetTable = {
    "tech_choice": (PS, (KN,)),
    "implementation": (KN, (OW,)),
    "troubleshooting": (PS, (OW, GR)),
    "integration": (KN, (PS,)),
    "structure": (PS, (KN,)),
}
JOB_TABLES: dict[str, TargetTable] = {
    "DATA_ANALYST": {
        "hypothesis": (PS, (KN,)),
        "data_prep": (KN, (PS,)),
        "method": (PS, (KN,)),
        "interpretation": (KN, (PS,)),
        "impact": (OW, (COL,)),
    },
    "DBA_INFRA": {
        "design": (PS, (KN,)),
        "performance": (PS, (KN, OW)),
        "operations": (KN, (OW,)),
        "incident": (PS, (OW, GR)),
        "automation": (OW, (KN,)),
    },
    "QA_ENGINEER": {
        "test_design": (KN, (PS,)),
        "defect_reporting": (OW, (COL,)),
        "release_judgment": (PS, (KN,)),
        "automation": (OW, (KN,)),
        "quality_process": (PS, (GR,)),
    },
    "SECURITY": {
        "vuln_principle": (KN, ()),
        "analysis_process": (OW, (KN,)),
        "mitigation": (PS, (KN,)),
        "incident_response": (PS, (OW,)),
        "ethics_law": (KN, (PS,)),
    },
    "PRODUCT_MANAGER": {
        "problem_definition": (PS, (KN,)),
        "prioritization": (PS, ()),
        "metrics": (KN, (PS,)),
        "collaboration": (COL, (OW,)),
        "retrospective": (GR, (OW,)),
    },
    "IT_CONSULTANT": {
        "requirement_analysis": (PS, (KN,)),
        "solution_design": (PS, (KN,)),
        "client_communication": (COL, (OW,)),
        "delivery": (OW, (COL,)),
        "outcome": (OW, (GR,)),
    },
    "GEN_STRATEGY": {
        "market_analysis": (KN, (PS,)),
        "business_plan": (PS, (KN,)),
        "budget_resource": (PS, (OW,)),
        "execution": (OW, (COL,)),
        "performance": (KN, (OW,)),
    },
    "GEN_HR_ADMIN": {
        "recruiting": (PS, (KN,)),
        "evaluation_reward": (KN, (PS,)),
        "training_culture": (OW, (COL,)),
        "labor_policy": (KN, (PS,)),
        "admin_support": (OW, (GR,)),
    },
    "GEN_FINANCE": {
        "closing": (KN, (OW,)),
        "tax_compliance": (KN, ()),
        "budget_cost": (PS, (KN,)),
        "financial_analysis": (KN, (PS,)),
        "treasury_risk": (PS, (KN,)),
    },
    "GEN_MARKETING": {
        "market_customer": (KN, (PS,)),
        "campaign_planning": (PS, (KN,)),
        "channel_execution": (OW, (PS,)),
        "performance_measure": (KN, (GR,)),
        "brand_comms": (COL, (PS,)),
    },
    "GEN_SALES": {
        "prospecting": (PS, (OW,)),
        "needs_proposal": (PS, (COL,)),
        "negotiation": (COL, (PS,)),
        "account_management": (COL, (OW,)),
        "sales_performance": (OW, (GR,)),
    },
    "GEN_DESIGN": {
        "research": (PS, (KN,)),
        "concept": (PS, ()),
        "design_decision": (PS, (KN,)),
        "collaboration": (COL, (GR,)),
        "validation": (OW, (GR,)),
    },
}
NON_TECH_TABLE: TargetTable = {
    "motivation": (MOT, ()),
    "collaboration": (COL, (GR,)),
    "growth": (GR, (OW,)),
    "judgment": (PS, ()),
    "ownership": (OW, (GR,)),
}


def normalize_category(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    return value if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value) else None


def resolve_targets(
    category: object, job_role: str | None = None, persona_role: str | None = None
) -> CompetencyTargets:
    category = normalize_category(category)
    if category is None:
        return CompetencyTargets(None)
    non_tech = persona_role is not None and persona_role.strip().upper() != "TECH"
    if not non_tech:
        table = JOB_TABLES.get((job_role or "").strip().upper(), DEV_TABLE)
        if category in table:
            primary, secondary = table[category]
            return CompetencyTargets(primary, secondary, "job")
    if category in NON_TECH_TABLE:
        primary, secondary = NON_TECH_TABLE[category]
        return CompetencyTargets(primary, secondary, "non_tech")
    return CompetencyTargets(None)


def infer_targets(raw: object, *, non_tech: bool) -> CompetencyTargets:
    try:
        primary = Competency(raw) if isinstance(raw, str) else None
    except ValueError:
        primary = None
    if non_tech and primary == KN:
        primary = None
    return CompetencyTargets(primary, (), "inferred" if primary else "unclassified")
