"""실제 LLM으로 고정 피드백 사례를 반복 채점한다.

실행: uv run python -m evaluation.run_feedback --output /tmp/feedback-runs.jsonl
이 명령은 Anthropic API를 호출하므로 비용과 호출 제한이 적용된다.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluation.cases import DEFAULT_CASES, load_cases

from app.core.common.feedback.multi.dto import FeedbackPersona
from app.core.common.feedback.multi.prompt import SYSTEM_PROMPT as MULTI_PROMPT
from app.core.common.feedback.multi.prompt import build_grading_user_message as multi_message
from app.core.common.feedback.multi.tools import SUBMIT_MULTI_FEEDBACK_TOOL
from app.core.common.feedback.scoring import parse_axis_levels, parse_consistency, summarize_session
from app.core.common.feedback.solo.dto import AssembledSession, GradingTarget
from app.core.common.feedback.solo.prompt import SYSTEM_PROMPT as SOLO_PROMPT
from app.core.common.feedback.solo.prompt import build_grading_user_message as solo_message
from app.core.common.feedback.solo.tools import SUBMIT_FEEDBACK_TOOL
from app.core.common.tool_use import extract_tool_input
from app.outbound.adapters.anthropic_text_client_impl import AnthropicTextClientImpl

def variants_for(mode: str, specs: list[str]) -> list[tuple[str, str]]:
    variants: list[tuple[str, str]] = []
    for spec in specs or ["current"]:
        if spec == "current":
            variants.append(("current", SOLO_PROMPT if mode == "solo" else MULTI_PROMPT))
            continue
        if "=" not in spec:
            raise ValueError("--variant는 current 또는 이름=시스템프롬프트파일 형식입니다")
        name, path = spec.split("=", 1)
        if not name or name == "current":
            raise ValueError("프롬프트 파일에는 current 외의 이름을 지정하세요")
        variants.append((name, Path(path).read_text(encoding="utf-8")))
    if len({name for name, _ in variants}) != len(variants):
        raise ValueError("variant 이름이 중복됐습니다")
    return variants


def assembled_case(case: dict[str, Any]) -> AssembledSession:
    by_id = {question["id"]: question for question in case["questions"]}
    targets: list[GradingTarget] = []
    for question in case["questions"]:
        parent = by_id.get(question.get("parent_id"))
        if question["type"] == "FOLLOW" and parent is None:
            raise ValueError(f"부모 질문이 없음: {case['id']}")
        targets.append(
            GradingTarget(
                question_id=question["id"],
                type=question["type"],
                intention=question["intention"],
                content=question["content"],
                answer=question["answer"],
                parent_question=parent["content"] if parent else None,
                parent_answer=parent["answer"] if parent else None,
            )
        )
    return AssembledSession(targets=tuple(targets), unanswered_question_ids=(), question_count=len(targets))


def grading_input(case: dict[str, Any], assembled: AssembledSession) -> tuple[str, dict[str, Any]]:
    if case["mode"] == "solo":
        return solo_message(assembled, None, 3000), SUBMIT_FEEDBACK_TOOL
    personas = tuple(FeedbackPersona(persona_id=item["id"], role=item["role"]) for item in case["personas"])
    persona_map = {item.persona_id: item for item in personas}
    by_question = {question["id"]: persona_map[question["persona_id"]] for question in case["questions"]}
    return multi_message(assembled, personas, by_question, 3000), SUBMIT_MULTI_FEEDBACK_TOOL


def parse_result(case: dict[str, Any], content_blocks: list[dict[str, Any]], tool_name: str) -> dict[str, Any]:
    raw = extract_tool_input(content_blocks, tool_name)
    if not isinstance(raw, dict) or not isinstance(raw.get("overall"), dict):
        raise ValueError("도구 호출 또는 overall 누락")
    overall = raw["overall"]
    if not isinstance(overall.get("summary"), str) or not overall["summary"].strip():
        raise ValueError("전체 요약 누락")
    for key in ("strengths", "improvements"):
        if not isinstance(overall.get(key), list) or not all(isinstance(value, str) for value in overall[key]):
            raise ValueError(f"전체 {key} 형식 오류")
    feedbacks = raw.get("feedbacks")
    if not isinstance(feedbacks, list):
        raise ValueError("feedbacks 누락")
    by_id = {item.get("question_id"): item for item in feedbacks if isinstance(item, dict)}
    expected = {question["id"] for question in case["questions"]}
    if set(by_id) != expected or len(feedbacks) != len(expected):
        raise ValueError("질문별 피드백 id 불일치")
    roles = {question["id"]: question.get("persona_id") for question in case["questions"]}
    persona_roles = {item["id"]: item["role"].strip().upper() for item in case.get("personas", [])}
    levels = []
    for question in case["questions"]:
        item = by_id[question["id"]]
        for key in ("model_answer", "comment"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise ValueError(f"문항 {question['id']}의 {key} 누락")
        for key in ("strengths", "improvements"):
            if not isinstance(item.get(key), list) or not all(isinstance(value, str) for value in item[key]):
                raise ValueError(f"문항 {question['id']}의 {key} 형식 오류")
        level = parse_axis_levels(item.get("scores"))
        if level is None:
            raise ValueError(f"축별 등급 오류: {question['id']}")
        if case["mode"] == "multi":
            persona_id = roles[question["id"]]
            if not isinstance(persona_id, str):
                raise ValueError(f"면접관 id 누락: {question['id']}")
            if persona_roles[persona_id] != "TECH":
                level = level.without_accuracy()
        levels.append(level)
    scores = summarize_session(levels, parse_consistency(raw["overall"].get("consistency")))
    if scores is None:
        raise ValueError("점수를 계산할 수 없음")
    if case["mode"] == "multi":
        persona_feedbacks = raw.get("personas")
        expected_personas = {item["id"] for item in case["personas"]}
        if not isinstance(persona_feedbacks, list) or len(persona_feedbacks) != len(expected_personas):
            raise ValueError("면접관별 피드백 누락")
        persona_ids = {item.get("persona_id") for item in persona_feedbacks if isinstance(item, dict)}
        if persona_ids != expected_personas:
            raise ValueError("면접관별 피드백 id 불일치")
    return {
        "total_score": scores.total_score,
        "axis_scores": scores.breakdown.axis_scores(),
        "question_levels": {question["id"]: level.as_dict() for question, level in zip(case["questions"], levels)},
        "consistency_score": scores.consistency,
        "overall": overall,
        "feedbacks": feedbacks,
    }


async def run(args: argparse.Namespace) -> None:
    cases = load_cases(args.cases, args.split)
    variants = {mode: variants_for(mode, args.variant) for mode in {case["mode"] for case in cases}}
    planned = sum(len(variants[case["mode"]]) * args.repeats for case in cases)
    if args.dry_run:
        print(f"validated_cases={len(cases)} planned_llm_calls={planned}")
        return
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY가 필요합니다")
    client = AnthropicTextClientImpl(api_key=api_key)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as output:
        for case in cases:
            assembled = assembled_case(case)
            user_message, tool = grading_input(case, assembled)
            for repeat in range(1, args.repeats + 1):
                for variant_name, system in variants[case["mode"]]:
                    record: dict[str, Any] = {
                        "case_id": case["id"], "split": case["split"], "mode": case["mode"],
                        "variant": variant_name, "repeat": repeat, "model": args.model,
                        "max_tokens": args.max_tokens,
                        "prompt_sha256": hashlib.sha256(system.encode()).hexdigest(),
                        "input_sha256": hashlib.sha256(user_message.encode()).hexdigest(),
                        "measured_at": datetime.now(UTC).isoformat(),
                    }
                    started = asyncio.get_running_loop().time()
                    try:
                        response = await client.call(
                            model=args.model, system=system, messages=[{"role": "user", "content": user_message}],
                            tools=[tool], tool_choice={"type": "tool", "name": tool["name"]},
                            max_tokens=args.max_tokens,
                        )
                        record.update({"stop_reason": response.stop_reason, "input_tokens": response.input_tokens,
                                       "output_tokens": response.output_tokens})
                        if response.stop_reason != "tool_use":
                            raise ValueError(f"예상하지 못한 종료 사유: {response.stop_reason}")
                        record.update(parse_result(case, response.content_blocks, tool["name"]))
                        record["status"] = "succeeded"
                    except Exception as exc:
                        record.update({"status": "failed", "error_type": type(exc).__name__})
                    record["duration_seconds"] = round(asyncio.get_running_loop().time() - started, 3)
                    output.write(json.dumps(record, ensure_ascii=False) + "\n")
                    output.flush()
                    print(f"{case['id']} {variant_name} {repeat}/{args.repeats}: {record['status']}")
    await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--split", choices=("development", "holdout", "all"), default="holdout")
    parser.add_argument("--variant", action="append", default=[], help="current 또는 이름=전체 시스템 프롬프트 파일")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--model", default="claude-sonnet-4-6")
    parser.add_argument("--max-tokens", type=int, default=14336)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true", help="API 호출 없이 입력과 호출 횟수만 확인")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats는 1 이상이어야 합니다")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
