from __future__ import annotations

from typing import Any

# solo/tools.py 를 그대로 재사용하지 않고 따로 두는 이유는 설명문이 달라야 하기 때문이다.
# N:1 은 직책이 채점 관점에 들어가고, reliability 의 초점이 "면접관이 바뀐 뒤의 진술 변화"로 옮겨간다.
# 스키마만 같고 모델에게 주는 지시가 다르므로, 공유하면 한쪽 문구가 다른 쪽을 망친다.
#
# 필드명을 snake_case 로 두는 이유는 solo 와 동일 —
# 파싱 결과를 그대로 결과 모델로 넘기기 위해서다(CamelModel 은 populate_by_name=True).
_ANSWER_FEEDBACK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "question_id": {
            "type": "string",
            "description": "채점 대상으로 전달받은 question_id 를 그대로 사용한다.",
        },
        "model_answer": {
            "type": "string",
            "description": (
                "40~100자의 짧은 예시 답안. 채점 기준이 아니라 사용자에게 보여주는 예시다. "
                "그 문항을 물은 면접관의 직책 관점에서 쓴다."
            ),
        },
        "strengths": {
            "type": "array",
            "items": {"type": "string"},
            "description": "답변에서 실제로 잘한 점. 없으면 빈 배열.",
        },
        "improvements": {
            "type": "array",
            "items": {"type": "string"},
            "description": "개선점. 질문 의도 중 답변이 다루지 않은 부분은 반드시 여기에 포함한다.",
        },
        "comment": {
            "type": "string",
            "description": "한 문장짜리 총평.",
        },
    },
    "required": ["question_id", "model_answer", "strengths", "improvements", "comment"],
}


_PERSONA_FEEDBACK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "persona_id": {
            "type": "string",
            "description": "전달받은 persona_id 를 그대로 사용한다. 새로 만들지 마라.",
        },
        "score": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
            "description": (
                "이 면접관이 담당한 문항들에 대한 점수. 그 직책의 관심사를 얼마나 충족했는지로 매긴다. "
                "담당 문항이 2~3개뿐이므로 전체 점수와 달라도 된다 — 오히려 같은 값으로 뭉치면 안 된다."
            ),
        },
        "comment": {
            "type": "string",
            "description": "이 면접관 시점의 한 문장짜리 총평. 두 문장 이상 쓰지 마라.",
        },
        "strengths": {
            "type": "array",
            "items": {"type": "string"},
            "description": "이 면접관이 보기에 좋았던 점. 1~2개. 없으면 빈 배열.",
        },
        "improvements": {
            "type": "array",
            "items": {"type": "string"},
            "description": "이 면접관이 보기에 아쉬운 점. 1~2개.",
        },
    },
    "required": ["persona_id", "score", "comment", "strengths", "improvements"],
}


_OVERALL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "total_score": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
            "description": "면접 전체에 대한 종합 평가 점수. 답변들의 전반적인 깊이·근거·완성도.",
        },
        "intent_alignment_score": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
            "description": (
                "질문이 물은 것에 실제로 답했는지만 본 점수. 답변 품질이 아니라 동문서답·빗나감 여부를 판단한다."
            ),
        },
        "reliability_score": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
            "description": (
                "일관성 점수. 답변끼리 모순이 없는지, 주장에 구체적 근거가 붙어 있는지를 본다. "
                "특히 면접관이 바뀐 뒤 같은 사안을 다르게 말했는지 주의해서 본다. "
                "질문 부합 여부는 여기에 반영하지 않는다."
            ),
        },
        "summary": {"type": "string", "description": "면접 전체에 대한 총평."},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "improvements": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "total_score",
        "intent_alignment_score",
        "reliability_score",
        "summary",
        "strengths",
        "improvements",
    ],
}


SUBMIT_MULTI_FEEDBACK_TOOL: dict[str, Any] = {
    "name": "submit_multi_feedback",
    "description": (
        "다대일 면접 답변의 채점 결과를 제출한다. 전달받은 모든 question_id 와 "
        "모든 persona_id 에 대해 빠짐없이 결과를 담아야 한다. 이 호출이 작업 종료 신호다."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "overall": _OVERALL_SCHEMA,
            "personas": {
                "type": "array",
                "description": "면접관별 평가. 담당 문항이 하나도 없는 면접관도 빠짐없이 담는다.",
                "items": _PERSONA_FEEDBACK_SCHEMA,
            },
            "feedbacks": {
                "type": "array",
                "description": "채점 대상 문항 각각에 대한 피드백.",
                "items": _ANSWER_FEEDBACK_SCHEMA,
            },
        },
        "required": ["overall", "personas", "feedbacks"],
    },
}
