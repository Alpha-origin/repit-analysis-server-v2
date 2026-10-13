"""실제 API 서버의 피드백 요청·조회 경로로 완료 시간을 측정한다.

격리된 환경의 본인 소유 면접 ID를 사용한다. 실행하면 피드백 작업과 모델 비용이 발생한다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

FEEDBACK_PATH = "/api/feedbacks"


def load_interview_ids(path: Path) -> list[int]:
    ids = [int(line.strip()) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not ids or any(value < 1 for value in ids) or len(ids) != len(set(ids)):
        raise ValueError("면접 ID는 양의 정수이며 중복 없이 한 줄에 하나씩 있어야 합니다")
    return ids


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[math.ceil(len(ordered) * fraction) - 1], 3)


def response_data(response: httpx.Response) -> dict[str, Any]:
    body = response.json()
    if not isinstance(body, dict) or body.get("success") is not True or not isinstance(body.get("data"), dict):
        raise ValueError("API 응답의 success/data 형식이 맞지 않습니다")
    return body["data"]


async def check_candidate(client: httpx.AsyncClient, interview_id: int) -> None:
    response = await client.get(FEEDBACK_PATH, params={"interviewId": interview_id})
    if response.status_code == 404:
        return
    response.raise_for_status()
    status = response_data(response).get("status")
    raise ValueError(f"면접 {interview_id}에 {status} 피드백이 이미 있습니다. 부하 실험을 중단합니다")


async def submit(client: httpx.AsyncClient, interview_id: int, semaphore: asyncio.Semaphore) -> dict[str, Any]:
    async with semaphore:
        started = time.time()
        record: dict[str, Any] = {"interview_id": interview_id, "started_at_epoch": started}
        try:
            response = await client.post(FEEDBACK_PATH, params={"interviewId": interview_id})
            record["http_status"] = response.status_code
            if response.status_code == 200:
                data = response_data(response)
                if data.get("status") != "accepted" or not isinstance(data.get("jobId"), str):
                    raise ValueError("접수 응답의 status/jobId가 맞지 않습니다")
                record["job_id"] = data["jobId"]
            else:
                record["error"] = "http_rejected"
        except (httpx.HTTPError, ValueError) as exc:
            record["error"] = type(exc).__name__
        record["accept_seconds"] = round(time.time() - started, 3)
        return record


async def poll(client: httpx.AsyncClient, interview_id: int) -> tuple[dict[str, Any] | None, str | None]:
    try:
        response = await client.get(FEEDBACK_PATH, params={"interviewId": interview_id})
        if response.status_code == 404:
            return None, None
        response.raise_for_status()
        return response_data(response), None
    except (httpx.HTTPError, ValueError) as exc:
        return None, type(exc).__name__


async def run(args: argparse.Namespace) -> None:
    ids = load_interview_ids(args.interview_ids)
    if args.concurrency < 1 or args.poll_interval <= 0 or args.completion_timeout <= 0 or args.request_timeout <= 0:
        raise ValueError("동시성은 1 이상, 시간 옵션은 모두 양수여야 합니다")
    if args.dry_run:
        print(f"validated_interviews={len(ids)} concurrency={args.concurrency} target=API_SERVER")
        return
    authorization = os.environ.get("API_AUTHORIZATION")
    if not authorization:
        raise ValueError("API_AUTHORIZATION에 테스트 계정의 Authorization 헤더 전체 값을 설정하세요")

    semaphore = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient(
        base_url=args.base_url,
        headers={"Authorization": authorization},
        timeout=args.request_timeout,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        # 모든 ID를 먼저 확인한다. 하나라도 진행 중·완료 상태라면 새 작업을 보내지 않는다.
        await asyncio.gather(*(check_candidate(client, interview_id) for interview_id in ids))
        submissions = await asyncio.gather(*(submit(client, interview_id, semaphore) for interview_id in ids))
        pending = {row["interview_id"]: row for row in submissions if row.get("job_id")}
        completed: list[dict[str, Any]] = []
        timed_out_ids: list[int] = []
        poll_errors: dict[int, str] = {}
        while pending:
            await asyncio.sleep(args.poll_interval)
            results = await asyncio.gather(*(poll(client, interview_id) for interview_id in pending))
            for interview_id, (data, error) in zip(list(pending), results):
                if error:
                    poll_errors[interview_id] = error
                if data is None or data.get("status") not in {"SUCCEEDED", "FAILED"}:
                    continue
                submission = pending.pop(interview_id)
                finished = {
                    "interview_id": interview_id,
                    "job_id": submission["job_id"],
                    "status": data["status"],
                    "end_to_end_seconds": round(time.time() - submission["started_at_epoch"], 3),
                    "score_breakdown_present": isinstance(data.get("scoreBreakdown"), dict),
                    "feedback_count": len(data.get("feedbacks") or []),
                }
                if args.include_feedback:
                    finished["feedback"] = data
                completed.append(finished)
            for interview_id, submission in list(pending.items()):
                if time.time() - submission["started_at_epoch"] >= args.completion_timeout:
                    pending.pop(interview_id)
                    timed_out_ids.append(interview_id)

    accepted_times = [row["accept_seconds"] for row in submissions if row.get("job_id")]
    complete_times = [row["end_to_end_seconds"] for row in completed]
    summary = {
        "measured_at": datetime.now(UTC).isoformat(),
        "target": "Repit-api-server /api/feedbacks",
        "requests": len(ids),
        "concurrency": args.concurrency,
        "accepted": len(accepted_times),
        "rejected": len(ids) - len(accepted_times),
        "succeeded": sum(row["status"] == "SUCCEEDED" for row in completed),
        "failed": sum(row["status"] == "FAILED" for row in completed),
        "timed_out": len(timed_out_ids),
        "accept_p50_seconds": percentile(accepted_times, 0.5),
        "accept_p95_seconds": percentile(accepted_times, 0.95),
        "completed_p50_seconds": percentile(complete_times, 0.5),
        "completed_p95_seconds": percentile(complete_times, 0.95),
        "score_breakdown_missing": sum(
            row["status"] == "SUCCEEDED" and not row["score_breakdown_present"] for row in completed
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {"summary": summary, "submissions": submissions, "completed": completed,
             "timed_out_interview_ids": sorted(timed_out_ids), "poll_errors": poll_errors},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="API 서버 주소")
    parser.add_argument("--interview-ids", type=Path, required=True, help="본인 소유 완료 면접 ID를 한 줄에 하나씩")
    parser.add_argument("--concurrency", type=int, required=True)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--completion-timeout", type=float, default=600.0)
    parser.add_argument("--request-timeout", type=float, default=30.0)
    parser.add_argument("--include-feedback", action="store_true", help="민감한 피드백 원문도 출력 파일에 저장")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
