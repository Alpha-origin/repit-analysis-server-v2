from __future__ import annotations

from typing import Any

# 필드명을 snake_case 로 두는 이유: 파싱 결과를 그대로 InterviewFeedbackResult 로 넘기기 위해서다.
# CamelModel 은 populate_by_name=True 라 파이썬 이름으로도 채울 수 있다.
#
# total_score / intent_alignment_score / reliability_score 는 LLM 이 매기지만
# frequent_words / answered_count / question_count 는 서버가 계산하므로 스키마에 넣지 않는다.
# (넣으면 LLM 이 채워버려 서버 계산값과 충돌한다.)
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
                "질문 의도를 충족하는 방향을 간결하게 제시한다."
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
                "일관성 점수. 답변끼리 모순이 없는지, 주장에 구체적 근거가 붙어 있는지만 본다. "
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


SUBMIT_FEEDBACK_TOOL: dict[str, Any] = {
    "name": "submit_feedback",
    "description": (
        "면접 답변 채점 결과를 제출한다. 전달받은 모든 question_id 에 대해 "
        "빠짐없이 결과를 담아야 한다. 이 호출이 작업 종료 신호다."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "overall": _OVERALL_SCHEMA,
            "feedbacks": {
                "type": "array",
                "description": "채점 대상 문항 각각에 대한 피드백.",
                "items": _ANSWER_FEEDBACK_SCHEMA,
            },
        },
        "required": ["overall", "feedbacks"],
    },
}
