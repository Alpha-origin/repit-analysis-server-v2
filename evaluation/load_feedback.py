"""실제 피드백 HTTP 접수부터 콜백 완료까지 동시 이용 성능을 측정한다."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from evaluation.cases import DEFAULT_CASES, load_cases


def request_body(case: dict[str, Any], session_id: str, callback_url: str) -> dict[str, Any]:
    created_at = "2026-10-01T00:00:00Z"
    questions: list[dict[str, Any]] = []
    answers: list[dict[str, Any]] = []
    for index, item in enumerate(case["questions"], 1):
        question = {
            "questionId": item["id"], "type": item["type"], "intention": item["intention"],
            "content": item["content"], "createdAt": created_at,
        }
        if item.get("parent_id"):
            question["parentId"] = item["parent_id"]
        if case["mode"] == "multi":
            question["personaId"] = item["persona_id"]
        questions.append(question)
        answers.append({
            "answerId": f"answer-{index}", "questionId": item["id"],
            "content": item["answer"], "createdAt": created_at,
        })
    body = {
        "sessionId": session_id, "interviewId": session_id, "userId": "synthetic-evaluation-user",
        "questions": questions, "answers": answers, "callbackUrl": callback_url,
    }
    if case["mode"] == "multi":
        body["personas"] = [{"personaId": item["id"], "role": item["role"]} for item in case["personas"]]
    return body


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[math.ceil(len(ordered) * fraction) - 1], 3)


def matching_callbacks(path: Path, session_ids: set[str]) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    found: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
            session_id = record["payload"]["sessionId"]
        except (KeyError, TypeError, json.JSONDecodeError):
            continue
        if session_id in session_ids:
            found[session_id] = record
    return found


async def run(args: argparse.Namespace) -> None:
    cases = load_cases(args.cases, args.split)
    if args.requests < 1 or args.concurrency < 1:
        raise ValueError("--requests와 --concurrency는 1 이상이어야 합니다")
    if args.request_timeout <= 0 or args.callback_wait <= 0:
        raise ValueError("--request-timeout과 --callback-wait는 양수여야 합니다")
    if not args.callback_url.startswith("https://"):
        raise ValueError("callbackUrl은 HTTPS여야 합니다")
    run_id = uuid.uuid4().hex[:12]
    planned = [(f"load-{run_id}-{index}", cases[index % len(cases)]) for index in range(args.requests)]
    if args.dry_run:
        print(f"validated_cases={len(cases)} planned_requests={len(planned)} concurrency={args.concurrency}")
        return

    semaphore = asyncio.Semaphore(args.concurrency)
    started_at: dict[str, float] = {}

    async def submit(client: httpx.AsyncClient, session_id: str, case: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            started = time.time()
            started_at[session_id] = started
            try:
                response = await client.post(
                    f"/feedback/{case['mode']}",
                    json=request_body(case, session_id, args.callback_url),
                )
                received = time.time()
                response_body = response.json() if response.status_code == 202 else None
                job_id = response_body.get("jobId") if isinstance(response_body, dict) else None
                return {
                    "session_id": session_id, "case_id": case["id"], "mode": case["mode"],
                    "http_status": response.status_code, "job_id": job_id,
                    "started_at_epoch": started, "accepted_at_epoch": received,
                    "accept_seconds": round(received - started, 3),
                }
            except (httpx.HTTPError, ValueError):
                return {
                    "session_id": session_id, "case_id": case["id"], "mode": case["mode"],
                    "http_status": None, "job_id": None, "started_at_epoch": started,
                    "accept_seconds": round(time.time() - started, 3), "error": "request_failed",
                }

    async with httpx.AsyncClient(base_url=args.base_url, timeout=args.request_timeout) as client:
        accepted = await asyncio.gather(*(submit(client, session_id, case) for session_id, case in planned))

    expected = {row["session_id"] for row in accepted if row["http_status"] == 202 and row["job_id"]}
    deadline = time.monotonic() + args.callback_wait
    callbacks: dict[str, dict[str, Any]] = {}
    while expected and time.monotonic() < deadline:
        callbacks = matching_callbacks(args.callback_file, expected)
        if len(callbacks) == len(expected):
            break
        await asyncio.sleep(0.5)

    completed: list[dict[str, Any]] = []
    for row in accepted:
        callback = callbacks.get(row["session_id"])
        if callback is None:
            continue
        payload = callback["payload"]
        overall = payload.get("result", {}).get("overall", {})
        completed.append({
            "session_id": row["session_id"], "case_id": row["case_id"],
            "job_id_matches": row["job_id"] == payload.get("jobId"),
            "callback_status": payload.get("status"),
            "end_to_end_seconds": round(callback["received_at_epoch"] - started_at[row["session_id"]], 3),
            "score_breakdown_present": isinstance(overall.get("scoreBreakdown"), dict),
            "feedback_count": len(payload.get("result", {}).get("feedbacks", [])),
        })

    accepted_times = [row["accept_seconds"] for row in accepted if row["http_status"] == 202]
    complete_times = [row["end_to_end_seconds"] for row in completed]
    summary = {
        "run_id": run_id, "measured_at": datetime.now(UTC).isoformat(),
        "base_url": args.base_url, "requests": args.requests, "concurrency": args.concurrency,
        "accepted": len(expected), "callbacks": len(completed),
        "http_202": sum(row["http_status"] == 202 for row in accepted),
        "http_rejected": sum(row["http_status"] is not None and row["http_status"] != 202 for row in accepted),
        "request_errors": sum(row["http_status"] is None for row in accepted),
        "succeeded": sum(row["callback_status"] == "succeeded" for row in completed),
        "failed": sum(row["callback_status"] == "failed" for row in completed),
        "missing_callbacks": len(expected) - len(completed),
        "accepted_p50_seconds": percentile(accepted_times, 0.5),
        "accepted_p95_seconds": percentile(accepted_times, 0.95),
        "completed_p50_seconds": percentile(complete_times, 0.5),
        "completed_p95_seconds": percentile(complete_times, 0.95),
        "score_breakdown_missing": sum(
            row["callback_status"] == "succeeded" and not row["score_breakdown_present"] for row in completed
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"summary": summary, "accepted": accepted, "completed": completed}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="분석 서버 주소 예: http://127.0.0.1:8000")
    parser.add_argument("--callback-url", required=True, help="분석 서버가 접근 가능한 HTTPS /capture 주소")
    parser.add_argument("--callback-file", type=Path, required=True, help="콜백 수집기의 JSONL 출력 파일")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--split", choices=("development", "holdout", "all"), default="holdout")
    parser.add_argument("--requests", type=int, required=True)
    parser.add_argument("--concurrency", type=int, required=True)
    parser.add_argument("--request-timeout", type=float, default=30.0)
    parser.add_argument("--callback-wait", type=float, default=300.0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
