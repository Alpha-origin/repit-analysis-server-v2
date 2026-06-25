
from __future__ import annotations

from typing import Any

READ_FILES_TOOL: dict[str, Any] = {
    "name": "read_files",
    "description": (
        "분석할 소스 파일 내용을 가져온다. 제공된 파일 트리에 존재하는 "
        "정확한 경로만 전달하라. 여러 파일을 한 번에 요청해 왕복을 줄여라."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "레포명/상대경로 형식",
            }
        },
        "required": ["paths"],
    },
}


# 최종 산출물 스키마 — 라우터 응답·콜백 페이로드와 동일한 형태.
# enum/min/max 값을 tool input_schema 에 박아 둠으로써 LLM 응답을 1차 검증한다.
# 2차 검증은 ``InterviewQaResult`` 모델이 한다.
GENERATE_RESULT_TOOL: dict[str, Any] = {
    "name": "generate_result",
    "description": "분석이 충분하면 호출한다. 프로젝트 요약 + 질문·모범답변을 제출한다. 이 호출이 세션 종료 신호다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "project_summary": {
                "type": "object",
                "properties": {
                    "overview": {
                        "type": "string",
                        "description": "프로젝트 전체 1~2문장 요약(포트폴리오+코드 근거).",
                    },
                    "repositories": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "repo": {"type": "string"},
                                "role": {"type": "string"},
                                "description": {"type": "string"},
                            },
                            "required": ["repo", "role", "description"],
                        },
                    },
                    "core_features": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "description": {"type": "string"},
                                "based_on": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["name", "description"],
                        },
                    },
                    "tech_stack": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["overview", "repositories", "core_features", "tech_stack"],
            },
            "interview": {
                "type": "array",
                "minItems": 5,
                "maxItems": 5,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer", "minimum": 1, "maximum": 5},
                        "category": {
                            "type": "string",
                            "enum": [
                                "tech_choice",
                                "implementation",
                                "troubleshooting",
                                "integration",
                                "structure",
                            ],
                        },
                        "question": {"type": "string"},
                        "expected_answer": {"type": "string"},
                        "based_on": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "description": "근거 파일 경로(또는 ['file_tree']) — 최소 1개 필수.",
                        },
                    },
                    "required": ["id", "category", "question", "expected_answer", "based_on"],
                },
            },
        },
        "required": ["project_summary", "interview"],
    },
}


# 세션에서 LLM 에 전달할 tool 목록.
STAGE4_TOOLS: list[dict[str, Any]] = [READ_FILES_TOOL, GENERATE_RESULT_TOOL]
