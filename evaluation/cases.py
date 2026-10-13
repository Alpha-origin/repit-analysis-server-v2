"""프롬프트·부하 실험에서 공통으로 쓰는 공개 합성 사례를 읽는다."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_CASES = Path(__file__).with_name("cases.json")


def load_cases(path: Path, split: str) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("중복된 사례 id가 있습니다")
    selected = [case for case in cases if split == "all" or case["split"] == split]
    if not selected:
        raise ValueError("선택한 split에 사례가 없습니다")
    for case in selected:
        if case["mode"] not in {"solo", "multi"}:
            raise ValueError(f"잘못된 mode: {case['id']}")
        questions = case["questions"]
        question_ids = [question["id"] for question in questions]
        if not questions or len(question_ids) != len(set(question_ids)):
            raise ValueError(f"질문이 없거나 id가 중복됨: {case['id']}")
        if any(not question["answer"].strip() for question in questions):
            raise ValueError(f"빈 답변: {case['id']}")
        for question in questions:
            if question["type"] not in {"ORIGINAL", "FOLLOW"}:
                raise ValueError(f"질문 유형 오류: {case['id']}/{question['id']}")
            parent_id = question.get("parent_id")
            if question["type"] == "FOLLOW" and (parent_id not in question_ids or parent_id == question["id"]):
                raise ValueError(f"부모 질문 오류: {case['id']}/{question['id']}")
            if question["type"] == "ORIGINAL" and parent_id:
                raise ValueError(f"최초 질문의 parent_id 오류: {case['id']}/{question['id']}")
        if case["mode"] == "multi":
            persona_ids = [persona["id"] for persona in case["personas"]]
            roles = [persona["role"].strip().upper() for persona in case["personas"]]
            if not persona_ids or len(persona_ids) != len(set(persona_ids)) or len(roles) != len(set(roles)):
                raise ValueError(f"면접관 id 또는 role 중복: {case['id']}")
            if any(question["persona_id"] not in persona_ids for question in questions):
                raise ValueError(f"질문에 연결되지 않은 면접관: {case['id']}")
    return selected
