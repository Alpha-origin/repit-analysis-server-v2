from __future__ import annotations

from typing import Any

# 필드명을 snake_case 로 두는 이유는 solo/tools.py 와 동일 —
# 파싱 결과를 그대로 GeneratedQuestion 으로 넘기기 위해서다.
#
# id 는 스키마에 없다. 서버가 채번한다. 모델에게 맡기면 원질문 id 와 겹치거나
# 1 부터 다시 매긴다. persona_id 도 문자열이라 모델이 그대로 옮겨 적을 보장이 없어서,
# 프롬프트에 매긴 면접관 번호(persona_index) 로 받고 서버가 실제 id 로 되돌린다.
_GENERATED_QUESTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "persona_index": {
            "type": "integer",
            "description": "이 질문을 할 면접관의 번호. 프롬프트에 제시된 번호를 그대로 쓴다.",
        },
        "category": {
            "type": "string",
            "description": (
                "질문 카테고리를 소문자 영문 스네이크케이스로. "
                "그 직책이 실제로 확인하려는 축을 나타내는 이름이어야 한다(예: motivation, impact)."
            ),
        },
        "question": {
            "type": "string",
            "description": (
                "면접관이 실제로 물을 법한 질문 본문. 한국어 존댓말. 200자를 넘기지 마라. 한 문항은 한 가지만 묻는다."
            ),
        },
        "expected_answer": {
            "type": "string",
            "description": (
                "이 질문으로 확인하려는 것. 정답이 아니라 '좋은 답변에 담겨야 할 요소'를 적는다. "
                "해당 직책의 관점에서 쓴다. 나중에 채점 기준으로 쓰인다."
            ),
        },
        "based_on": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "질문의 근거. 프로젝트 요약에서 참조한 기능·기술 이름이 있으면 그것을 담고, "
                "없으면 면접관 직책만 담는다. 최소 1개는 반드시 채운다."
            ),
        },
    },
    "required": ["persona_index", "category", "question", "expected_answer", "based_on"],
}


SUBMIT_GENERATED_QUESTIONS_TOOL: dict[str, Any] = {
    "name": "submit_generated_questions",
    "description": (
        "비개발 직군 면접관이 물을 질문을 제출한다. 면접관마다 정해진 개수만큼 "
        "빠짐없이 담아야 한다. 이 호출이 작업 종료 신호다."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "description": "생성한 질문 목록. 면접관 번호로 누구의 질문인지 구분한다.",
                "items": _GENERATED_QUESTION_SCHEMA,
            },
        },
        "required": ["questions"],
    },
}
