from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.core.common.interview_qa.dto import QuestionCategory

TOOL_NAME = "submit_question_cycle"

# 필드명을 snake_case 로 두는 이유는 다른 tools.py 와 같다 — 파싱 결과를 그대로 CycleQuestion 으로 넘긴다.


def build_submit_question_cycle_tool(available: Sequence[QuestionCategory]) -> dict[str, Any]:
    """category enum 을 이용 가능한 카테고리로 좁힌 도구 스키마.

    재료 없는 카테고리를 프롬프트로만 막으면 모델이 종종 채워 넣는다.
    스키마 단계에서 1차로 막고, 서버 검증(rules.py) 이 2차로 막는다.
    """
    return {
        "name": TOOL_NAME,
        "description": "사이클 전체 질문을 한 번에 제출한다. 이 호출이 작업 종료 신호다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "set_no": {"type": "integer", "minimum": 1, "maximum": 3},
                            "category": {"type": "string", "enum": list(available)},
                            "question": {
                                "type": "string",
                                "description": "면접 질문 본문. 한국어 존댓말, 200자 이내, 한 가지만 묻는다.",
                            },
                            "intention": {
                                "type": "string",
                                "description": "이 질문으로 확인하려는 것 한 문장. 채점 기준이 된다.",
                            },
                            "expected_answer": {
                                "type": "string",
                                "description": "모범답안 400자 이내. 꼬리질문 생성과 참고용.",
                            },
                            "based_on": {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 1,
                                "description": "근거 경로 목록에 있는 경로를 그대로. 최소 1개.",
                            },
                        },
                        "required": ["set_no", "category", "question", "intention", "expected_answer", "based_on"],
                    },
                },
            },
            "required": ["questions"],
        },
    }
