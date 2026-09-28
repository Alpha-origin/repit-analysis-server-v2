from __future__ import annotations

from typing import Any

# 필드명을 snake_case 로 두는 이유: 파싱 결과를 그대로 InterviewFeedbackResult 로 넘기기 위해서다.
# CamelModel 은 populate_by_name=True 라 파이썬 이름으로도 채울 수 있다.
#
# total_score / intent_alignment_score / reliability_score 와
# frequent_words / answered_count / question_count 는 서버가 계산하므로 스키마에 넣지 않는다.
# (넣으면 LLM 이 채워버려 서버 계산값과 충돌한다.)
#
# 점수는 LLM 이 아니라 서버가 등급으로 계산한다(core/common/feedback/scoring.py).
# 등급 기준 문구는 시스템 프롬프트의 [축별 등급 기준] 한 곳에만 둔다 — 두 곳에 두면 한쪽만 고쳐져 어긋난다.
_LEVEL_SCHEMA: dict[str, Any] = {"type": "integer", "enum": [0, 1, 2, 3, 4]}

_AXIS_SCORES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "축별 등급(0~4). 기준은 시스템 프롬프트의 [축별 등급 기준]을 따른다.",
    "properties": {
        "intent": {**_LEVEL_SCHEMA, "description": "의도 충족. 0 이면 나머지 축도 0."},
        "depth": {**_LEVEL_SCHEMA, "description": "깊이 — 이유, 대안 비교, 한계 인식."},
        "specificity": {**_LEVEL_SCHEMA, "description": "구체성 — 실제로 한 일과 방식, 결과."},
        "accuracy": {
            "type": ["integer", "null"],
            "enum": [0, 1, 2, 3, 4, None],
            "description": "정확성 — 확립된 기술 개념 기준. 기술 내용이 없는 질문이면 null.",
        },
    },
    "required": ["intent", "depth", "specificity", "accuracy"],
}


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
        # strengths/improvements/comment 뒤에 둔다. 도구 호출이 강제라 모델이 따로 추론할 자리가 없어서,
        # 글로 된 평가를 먼저 쓰고 등급을 매기게 하는 편이 판정이 덜 흔들린다.
        "scores": _AXIS_SCORES_SCHEMA,
    },
    "required": ["question_id", "model_answer", "strengths", "improvements", "comment", "scores"],
}


_OVERALL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "면접 전체에 대한 총평."},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "improvements": {"type": "array", "items": {"type": "string"}},
        "consistency": {
            "type": ["integer", "null"],
            "enum": [0, 1, 2, 3, 4, None],
            "description": "세션 단위 일관성 등급 — 답변끼리 어긋나는 진술이 있는가. 답변이 1개뿐이면 null.",
        },
    },
    "required": ["summary", "strengths", "improvements", "consistency"],
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
