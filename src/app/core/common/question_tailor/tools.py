from __future__ import annotations

from typing import Any

# 필드명을 snake_case 로 두는 이유는 solo/tools.py 와 동일 —
# 파싱 결과를 그대로 TailoredQuestion 으로 넘기기 위해서다(CamelModel 은 populate_by_name=True).
#
# id 는 새로 만드는 값이 아니라 전달받은 원질문 id 다. 스키마 description 에서 못박지 않으면
# 모델이 1부터 다시 매기거나 순서를 바꿔 담는다.
_TAILORED_QUESTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {
            "type": "integer",
            "description": "재작성 대상으로 전달받은 원질문 id 를 그대로 사용한다. 새로 매기지 마라.",
        },
        "question": {
            "type": "string",
            "description": (
                "재작성된 질문 본문. 원질문이 검증하려던 대상은 그대로 두고 표현·맥락만 바꾼다. "
                "한 문항은 한 가지만 묻는다. 200자를 넘기지 마라."
            ),
        },
    },
    "required": ["id", "question"],
}


SUBMIT_TAILORED_QUESTIONS_TOOL: dict[str, Any] = {
    "name": "submit_tailored_questions",
    "description": (
        "재작성한 면접 질문을 제출한다. 전달받은 모든 원질문 id 에 대해 "
        "하나씩, 빠짐없이 담아야 한다. 질문을 추가하거나 빼지 마라. 이 호출이 작업 종료 신호다."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "description": "원질문과 1:1 로 대응하는 재작성 결과.",
                "items": _TAILORED_QUESTION_SCHEMA,
            },
        },
        "required": ["questions"],
    },
}
