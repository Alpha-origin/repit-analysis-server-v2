from typing import Any

from app.core.common.company.fit.dto import FIT_COMMENT_MAX, FIT_ITEM_TEXT_MAX, FIT_ITEMS_MAX

TOOL_NAME = "submit_company_fit"


def _items_schema(text_field: str) -> dict[str, Any]:
    return {
        "type": "array",
        "maxItems": FIT_ITEMS_MAX,
        "items": {
            "type": "object",
            "properties": {
                "fact_id": {"type": "string"},
                text_field: {"type": "string", "minLength": 1, "maxLength": FIT_ITEM_TEXT_MAX},
                "question_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}},
            },
            "required": ["fact_id", text_field, "question_ids"],
            "additionalProperties": False,
        },
    }


SUBMIT_COMPANY_FIT_TOOL: dict[str, Any] = {
    "name": TOOL_NAME,
    "strict": True,
    "description": "답변 근거에 기반한 기업 관점 코멘트를 제출한다 점수나 등급을 포함하지 않는다",
    "input_schema": {
        "type": "object",
        "properties": {
            "comment": {"type": "string", "minLength": 1, "maxLength": FIT_COMMENT_MAX},
            "matched_focus": _items_schema("evidence"),
            "gaps": _items_schema("suggestion"),
        },
        "required": ["comment", "matched_focus", "gaps"],
        "additionalProperties": False,
    },
}
