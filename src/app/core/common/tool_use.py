from __future__ import annotations

import json
from typing import Any


def extract_tool_input(content_blocks: list[dict[str, Any]], tool_name: str) -> dict[str, Any] | None:
    """응답 블록에서 지정한 도구의 입력(dict) 을 꺼낸다. 없거나 깨졌으면 None.

    tool_choice 로 도구 호출을 강제해도 블록 순서·형태는 보장되지 않아서,
    도구 이름으로 직접 찾아야 한다. 채점·재작성 등 tool-use 를 쓰는 모든 단계가 공유한다.
    """
    for block in content_blocks:
        if block.get("type") != "tool_use" or block.get("name") != tool_name:
            continue
        raw_input = block.get("input")
        # SDK 가 dict 로 주는 경우와, 드물게 문자열로 주는 경우를 모두 대비.
        if isinstance(raw_input, str):
            try:
                raw_input = json.loads(raw_input)
            except json.JSONDecodeError:
                return None
        return raw_input if isinstance(raw_input, dict) else None
    return None
