from typing import Any

from app.core.common.company.fit.dto import FIT_COMMENT_MAX, FIT_ITEM_TEXT_MAX, FIT_ITEMS_MAX

TOOL_NAME = "submit_company_fit"


def _items_schema(text_field: str) -> dict[str, Any]:
    return {
        "type": "array",
        "description": f"최대 {FIT_ITEMS_MAX}개. 답변 근거가 없으면 빈 배열.",
        "items": {
            "type": "object",
            "properties": {
                "fact_id": {"type": "string"},
                text_field: {"type": "string", "description": f"답변에 근거한 한국어 문장. 1~{FIT_ITEM_TEXT_MAX}자."},
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
            # strict가 지원하지 않는 길이·개수 상한은 설명에만 두고 파서가 검증한다.
            "comment": {"type": "string", "description": f"기업 관점 총평. 1~2문장 한국어, 1~{FIT_COMMENT_MAX}자."},
            "matched_focus": _items_schema("evidence"),
            "gaps": _items_schema("suggestion"),
        },
        "required": ["comment", "matched_focus", "gaps"],
        "additionalProperties": False,
    },
}
