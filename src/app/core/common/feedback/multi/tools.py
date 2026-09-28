from __future__ import annotations

from typing import Any

# solo/tools.py 를 그대로 재사용하지 않고 따로 두는 이유는 설명문이 달라야 하기 때문이다.
# N:1 은 직책이 채점 관점에 들어가고, 일관성의 초점이 "면접관이 바뀐 뒤의 진술 변화"로 옮겨간다.
# 스키마만 같고 모델에게 주는 지시가 다르므로, 공유하면 한쪽 문구가 다른 쪽을 망친다.
#
# 필드명을 snake_case 로 두는 이유는 solo 와 동일 —
# 파싱 결과를 그대로 결과 모델로 넘기기 위해서다(CamelModel 은 populate_by_name=True).
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
            "description": (
                "정확성 — 확립된 기술 개념 기준. 기술 내용이 없는 질문이면 null. "
                "직책이 TECH 가 아닌 면접관의 문항은 서버가 null 로 처리한다."
            ),
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
        # strengths/improvements/comment 뒤에 둔다. 도구 호출이 강제라 모델이 따로 추론할 자리가 없어서,
        # 글로 된 평가를 먼저 쓰고 등급을 매기게 하는 편이 판정이 덜 흔들린다.
        "scores": _AXIS_SCORES_SCHEMA,
    },
    "required": ["question_id", "model_answer", "strengths", "improvements", "comment", "scores"],
}


# 면접관별 점수도 서버가 담당 문항의 등급으로 계산한다. 여기에는 글로 된 평가만 받는다.
_PERSONA_FEEDBACK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "persona_id": {
            "type": "string",
            "description": "전달받은 persona_id 를 그대로 사용한다. 새로 만들지 마라.",
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
            "description": "이 면접관이 보기에 아쉬운 점. 1~2개. 담당 답변이 없으면 빈 배열.",
        },
    },
    "required": ["persona_id", "comment", "strengths", "improvements"],
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
            "description": (
                "세션 단위 일관성 등급 — 면접관이 바뀐 뒤를 포함해 답변끼리 어긋나는 진술이 있는가. "
                "답변이 1개뿐이면 null."
            ),
        },
    },
    "required": ["summary", "strengths", "improvements", "consistency"],
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
