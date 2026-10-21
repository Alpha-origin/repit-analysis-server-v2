from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.core.common.feedback.competency.catalog import Competency
from app.core.common.feedback.competency.evidence import group_schema
from app.core.common.feedback.multi.tools import feedback_tool_for_job as multi_tool
from app.core.common.feedback.solo.tools import feedback_tool_for_job as solo_tool


def feedback_tool(job_role: str | None, *, multi: bool = False) -> dict[str, Any]:
    tool = deepcopy(multi_tool(job_role) if multi else solo_tool(job_role))
    props = tool["input_schema"]["properties"]
    answer = props["feedbacks"]["items"]
    for name in ("scores", "depth_evidence"):
        del answer["properties"][name]
        answer["required"].remove(name)
    answer["properties"].update(
        {
            "response": {"type": "integer", "enum": list(range(5))},
            "inferred_primary": {"anyOf": [{"type": "string", "enum": list(Competency)}, {"type": "null"}]},
            "evidence": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    comp.value.lower(): {"anyOf": [group_schema(comp), {"type": "null"}]} for comp in Competency
                },
                "required": [comp.value.lower() for comp in Competency],
            },
        }
    )
    answer["required"].extend(("response", "inferred_primary", "evidence"))
    props["overall"]["properties"]["consistency_issues"] = {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "kind": {"type": "string", "enum": ["ROLE", "TECH_CHOICE", "CAUSE", "DETAIL", "OTHER"]},
                "question_ids": {"type": "array", "items": {"type": "string"}},
                "quotes": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["kind", "question_ids", "quotes"],
        },
    }
    props["overall"]["required"].append("consistency_issues")
    return tool
