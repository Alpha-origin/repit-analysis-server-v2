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
