from __future__ import annotations

from typing import Any

from app.core.common.interview_qa.tools import READ_FILES_TOOL

# 필드명을 snake_case 로 두는 이유는 interview_qa/tools.py 와 같다 —
# 제출값을 그대로 ApplicantProfile 로 검증한다(CamelModel 은 populate_by_name=True).
# schema_version 과 major 는 서버가 채우므로 스키마에 없다.

_EVIDENCE_SCHEMA: dict[str, Any] = {
    "type": "array",
    "description": "근거. read_files 로 실제로 읽은 파일 경로만 read_files 에 전달한 형식 그대로 넣는다.",
    "items": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "레포명/상대경로"},
            "note": {"type": "string", "description": "이 파일에서 확인한 것 한 줄"},
        },
        "required": ["path", "note"],
    },
}


SUBMIT_PROFILE_TOOL: dict[str, Any] = {
    "name": "submit_profile",
    "description": (
        "분석이 충분하면 호출한다. 포트폴리오와 코드를 대조해 확인한 종합 데이터를 제출한다. "
        "이 호출이 세션 종료 신호다."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "overview": {
                "type": "string",
                "description": "프로젝트 전체 1~2문장 요약(포트폴리오+코드 근거).",
            },
            "tech_stack": {
                "type": "array",
                "description": "실제로 쓰인 기술. 근거는 그 기술을 도입·설정한 파일(build.gradle, package.json 등).",
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "evidence": _EVIDENCE_SCHEMA},
                    "required": ["name", "evidence"],
                },
            },
            "repositories": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "repo": {"type": "string"},
                        "role": {"type": "string"},
                        "description": {"type": "string"},
                        "tech_stack": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["repo", "role", "description", "tech_stack"],
                },
            },
            "core_features": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "implementation": {"type": "string", "description": "코드에서 확인한 구현 방식"},
                        "evidence": _EVIDENCE_SCHEMA,
                    },
                    "required": ["name", "description", "implementation", "evidence"],
                },
            },
            "troubleshootings": {
                "type": "array",
                "description": "포트폴리오의 문제 해결 서술 중 코드에서 해결이 확인된 것만 담는다.",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "portfolio_claim": {"type": "string", "description": "포트폴리오가 서술한 문제와 해결"},
                        "resolution_in_code": {"type": "string", "description": "해결이 코드에 반영된 방식"},
                        "evidence": _EVIDENCE_SCHEMA,
                    },
                    "required": ["title", "portfolio_claim", "resolution_in_code", "evidence"],
                },
            },
            "integrations": {
                "type": "array",
                "description": "레포, 서버, DB, 외부 API 사이의 연동.",
                "items": {
                    "type": "object",
                    "properties": {
                        "from": {"type": "string"},
                        "to": {"type": "string"},
                        "method": {"type": "string", "description": "HTTP, 메시지 큐, SDK 등 연동 방식"},
                        "evidence": _EVIDENCE_SCHEMA,
                    },
                    "required": ["from", "to", "method", "evidence"],
                },
            },
            "claim_checks": {
                "type": "array",
                "description": "포트폴리오의 주장(역할, 성과, 기술, 해결 경험)을 코드와 대조한 결과.",
                "items": {
                    "type": "object",
                    "properties": {
                        "claim": {"type": "string"},
                        "status": {"type": "string", "enum": ["confirmed", "partial", "unverified"]},
                        "note": {"type": "string"},
                    },
                    "required": ["claim", "status", "note"],
                },
            },
            "structure_notes": {
                "type": "array",
                "description": "저장소별 전체 구조. 구조 질문의 재료가 된다.",
                "items": {
                    "type": "object",
                    "properties": {
                        "repo": {"type": "string"},
                        "summary": {"type": "string"},
                        "key_paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "구조를 대표하는 파일 또는 디렉터리 경로. 파일 트리에 있는 레포명/상대경로.",
                        },
                    },
                    "required": ["repo", "summary", "key_paths"],
                },
            },
        },
        "required": [
            "overview",
            "tech_stack",
            "repositories",
            "core_features",
            "troubleshootings",
            "integrations",
            "claim_checks",
            "structure_notes",
        ],
    },
}


# 세션에서 LLM 에 전달할 tool 목록. read_files 는 /generate 와 같은 것을 쓴다.
PROFILE_TOOLS: list[dict[str, Any]] = [READ_FILES_TOOL, SUBMIT_PROFILE_TOOL]
