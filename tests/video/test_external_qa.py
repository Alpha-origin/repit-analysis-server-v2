from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from tests.video.qa_external import LEGACY_PATHS, SCENARIOS, ExternalSuite, Step, run_step, run_suite, safe_summary


def suite_data() -> dict[str, Any]:
    return {
        "analysis_base_url": "https://analysis.example.com",
        "receiver_base_url": "https://api.example.com",
        "scenarios": [
            {
                "name": name,
                "steps": [
                    {
                        "target": "analysis",
                        "method": "POST",
                        "path": path,
                        "body": {},
                        "expected_status": 202,
                        "expected": {"jobId": "job"},
                    }
                    for path in (sorted(LEGACY_PATHS) if name == "legacy-callbacks" else ["/analysis/video"])
                ],
            }
            for name in sorted(SCENARIOS)
        ],
    }


def test_missing_scenarios_and_insecure_bases_cannot_pass() -> None:
    incomplete = suite_data()
    incomplete["scenarios"].pop()
    with pytest.raises(ValidationError):
        ExternalSuite.model_validate(incomplete)
    insecure = {**suite_data(), "analysis_base_url": "http://analysis.example.com"}
    with pytest.raises(ValidationError):
        ExternalSuite.model_validate(insecure)


@pytest.mark.asyncio
async def test_runner_does_not_follow_redirect_or_report_partial_success() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(302, headers={"Location": "https://other.example.com"})

    suite = ExternalSuite.model_validate(suite_data())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        result = await run_suite(suite, {"analysis": "secret"}, client)
    assert result["result"] == "FAIL"
    assert len(result["scenarios"]) == 6
    assert all(request.url.host == "analysis.example.com" for request in calls)


@pytest.mark.asyncio
async def test_poll_and_callback_body_equality_use_saved_result() -> None:
    saved: dict[str, Any] = {
        "accepted": {"jobId": "job"},
        "snapshot": {"result": {"jobId": "job", "status": "unavailable"}},
    }
    suite = ExternalSuite.model_validate(suite_data())
    step = Step(
        target="receiver",
        path="/qa/callbacks/{accepted.jobId}",
        expected_status=200,
        expected={"body": "$snapshot.result"},
        minimum={"retryDelaySeconds": 5},
        poll_seconds=1,
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"body": saved["snapshot"]["result"], "retryDelaySeconds": 5.015})
        )
    ) as client:
        result = await run_step(client, suite, step, saved, {"receiver": "receiver-secret"})
    assert result["httpStatus"] == 200


def test_evidence_strips_raw_tokens_and_signed_urls() -> None:
    result = safe_summary(
        {"tokenOk": True, "echo": "secret-value", "url": "https://s3.example.com/a?sig=private"},
        {"analysis": "secret-value"},
    )
    assert result["tokenOk"] is True
    assert "secret-value" not in str(result)
    assert "private" not in str(result)
