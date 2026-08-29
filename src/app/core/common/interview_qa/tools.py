from __future__ import annotations

from typing import Any

from anthropic import transform_schema

from app.core.common.interview_qa.dto import InterviewQaResult

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


# 최종 산출물 스키마는 Pydantic 모델에서 생성한다. DTO와 도구 스키마를 별도로 관리하면
# 한쪽에만 필수 필드나 enum이 추가되는 드리프트가 생기므로 InterviewQaResult를 단일 원본으로 쓴다.
# transform_schema는 strict tool use가 지원하지 않는 일부 제약을 description으로 옮기고,
# 모든 object에 additionalProperties=false를 추가한다. 원본 제약은 서버의 Pydantic 검증이 재확인한다.
_GENERATE_RESULT_SCHEMA = transform_schema(InterviewQaResult.model_json_schema(by_alias=False))

GENERATE_RESULT_TOOL: dict[str, Any] = {
    "name": "generate_result",
    "description": "분석이 충분하면 호출한다. 프로젝트 요약 + 질문·모범답변을 제출한다. 이 호출이 세션 종료 신호다.",
    "strict": True,
    "input_schema": _GENERATE_RESULT_SCHEMA,
}


# 세션에서 LLM 에 전달할 tool 목록.
STAGE4_TOOLS: list[dict[str, Any]] = [READ_FILES_TOOL, GENERATE_RESULT_TOOL]
