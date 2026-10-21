from typing import Any

from app.core.common.feedback.competency.catalog import Competency
from app.core.common.feedback.competency.evidence import SLOTS
from tests.multi_helpers import feedback_output


def group(comp: Competency, **quotes: Any) -> dict[str, Any]:
    result: dict[str, Any] = dict.fromkeys(SLOTS[comp], "")
    if comp == Competency.KNOWLEDGE:
        result["accuracy"] = None
    result.update(quotes)
    return result


def output(comp: Competency = Competency.PROBLEM_SOLVING, **quotes: Any) -> dict[str, Any]:
    result = feedback_output()
    result["overall"]["consistency_issues"] = []
    entry = result["feedbacks"][0]
    entry.pop("scores", None)
    entry.pop("depth_evidence", None)
    entry.setdefault("strengths", [])
    entry.setdefault("improvements", [])
    entry.update(
        {"response": 4, "inferred_primary": None, "evidence": dict.fromkeys(c.value.lower() for c in Competency)}
    )
    entry["evidence"][comp.value.lower()] = group(comp, **quotes)
    return result
