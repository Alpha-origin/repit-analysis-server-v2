"""실제 API 서버의 피드백 요청·조회 계약을 모의 HTTP로 검증한다."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from evaluation import load_feedback


class LoadFeedbackTests(unittest.TestCase):
    def test_existing_feedback_stops_before_post(self) -> None:
        posts = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                posts.append(request)
            return httpx.Response(200, json={"success": True, "data": {"status": "PENDING"}})

        async def check() -> None:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://api") as client:
                await load_feedback.check_candidate(client, 12)

        with self.assertRaises(ValueError):
            asyncio.run(check())
        self.assertEqual(posts, [])

    def test_request_then_poll_real_api_paths(self) -> None:
        calls: list[tuple[str, str]] = []
        started = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal started
            calls.append((request.method, request.url.path))
            self.assertEqual(request.url.params["interviewId"], "12")
            self.assertEqual(request.headers["Authorization"], "Bearer synthetic")
            if request.method == "POST":
                started = True
                return httpx.Response(200, json={
                    "success": True, "data": {"jobId": "job-1", "sessionId": "session-1", "status": "accepted"}
                })
            if not started:
                return httpx.Response(404)
            return httpx.Response(200, json={
                "success": True,
                "data": {"status": "SUCCEEDED", "scoreBreakdown": {"scoringVersion": "axis-v1"},
                         "feedbacks": [{"questionId": "q1"}]},
            })

        client_type = httpx.AsyncClient
        transport = httpx.MockTransport(handler)
        with tempfile.TemporaryDirectory() as directory:
            ids = Path(directory) / "ids.txt"
            output = Path(directory) / "result.json"
            ids.write_text("12\n", encoding="utf-8")
            args = argparse.Namespace(
                base_url="http://api", interview_ids=ids, concurrency=1, poll_interval=0.001,
                completion_timeout=1.0, request_timeout=1.0, include_feedback=False,
                output=output, dry_run=False,
            )
            with patch.dict(os.environ, {"API_AUTHORIZATION": "Bearer synthetic"}):
                with patch.object(load_feedback.httpx, "AsyncClient", side_effect=lambda **kw: client_type(
                    transport=transport, **kw
                )):
                    asyncio.run(load_feedback.run(args))
            result = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(calls, [("GET", "/api/feedbacks"), ("POST", "/api/feedbacks"),
                                 ("GET", "/api/feedbacks")])
        self.assertEqual(result["summary"]["succeeded"], 1)
        self.assertEqual(result["summary"]["score_breakdown_missing"], 0)
        self.assertEqual(result["completed"][0]["feedback_count"], 1)


if __name__ == "__main__":
    unittest.main()
