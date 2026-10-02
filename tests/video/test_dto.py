from __future__ import annotations

import json
from typing import Any

import pytest

from app.core.common.video.dto import (
    AnalysisOutcome,
    JobSnapshot,
    TerminalCallback,
    VideoResult,
    public_error,
)


def callback(status: str = "ready", result: dict[str, Any] | None = None, error: Any = None) -> dict[str, Any]:
    return {
        "schemaVersion": "1",
        "jobId": "job-1",
        "requestId": "req-1",
        "sessionId": "session-1",
        "interviewId": "42",
        "userId": "7",
        "status": status,
        "result": result,
        "error": error,
    }


def result(status: str = "ready", **overrides: Any) -> dict[str, Any]:
    analysis: dict[str, Any] = {"status": status, "data": {"x": 1}, "error": None}
    if status == "unavailable":
        analysis = {"status": "unavailable", "data": None, "error": public_error("ANALYZER_NOT_CONFIGURED").wire()}
    return {
        "videoId": "video-1",
        "durationMs": 4000,
        "status": status,
        "analysis": analysis,
        "error": None,
        **overrides,
    }


def test_terminal_and_snapshot_roundtrip() -> None:
    file_error = public_error("VIDEO_DECODE_FAILED").wire()
    bodies = [
        callback("ready", result("ready")),
        callback("partial", result("partial")),
        callback("unavailable", result("unavailable")),  # analyzer not configured
        callback(
            "unavailable",
            result(
                "unavailable",
                durationMs=None,
                analysis={"status": "unavailable", "data": None, "error": file_error},
                error=file_error,
            ),
        ),
        callback("failed", None, public_error("INTERNAL_ERROR").wire()),
    ]
    for body in bodies:
        parsed = TerminalCallback.model_validate(body)
        assert parsed.wire() == body
        assert TerminalCallback.model_validate(json.loads(json.dumps(parsed.wire()))) == parsed
        status = "failed" if body["status"] == "failed" else "completed"
        snapshot = JobSnapshot.model_validate(
            {
                "jobId": "job-1",
                "requestId": "req-1",
                "sessionId": "session-1",
                "status": status,
                "createdAt": "2026-10-02T00:00:00.000Z",
                "finishedAt": "2026-10-02T00:01:00.000Z",
                "callbackStatus": "pending",
                "result": body,
                "error": body["error"] if status == "failed" else None,
            }
        )
        assert snapshot.wire()["result"] == body


def test_failed_snapshot_keeps_entire_failure_callback() -> None:
    error = public_error("INTERNAL_ERROR").wire()
    snapshot = JobSnapshot.model_validate(
        {
            "jobId": "job-1",
            "requestId": "req-1",
            "sessionId": "session-1",
            "status": "failed",
            "createdAt": "2026-10-02T00:00:00.000Z",
            "finishedAt": "2026-10-02T00:01:00.000Z",
            "callbackStatus": "failed",
            "result": callback("failed", None, error),
            "error": error,
        }
    ).wire()
    assert snapshot["status"] == "failed"
    assert snapshot["result"] is not None
    assert snapshot["result"]["status"] == "failed"
    assert snapshot["result"]["result"] is None
    assert snapshot["error"] == snapshot["result"]["error"]


@pytest.mark.parametrize(
    "body",
    [
        callback("failed", result("ready"), public_error("INTERNAL_ERROR").wire()),
        callback("failed", None, None),
        callback("ready", None, None),
        callback("ready", result("partial")),
        callback("ready", result("ready"), public_error("INTERNAL_ERROR").wire()),
        callback("ready", {**result("ready"), "status": "unavailable"}),
        callback("unavailable", result("unavailable", error=public_error("VIDEO_DECODE_FAILED").wire())),
        callback("ready", result("ready", durationMs=-1)),
        callback("ready", {**result("ready"), "behaviorScore": 3}),
        {**callback("ready", result("ready")), "schemaVersion": "2"},
    ],
)
def test_invalid_terminal_combinations(body: dict[str, Any]) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 — any validation error
        TerminalCallback.model_validate(body)


@pytest.mark.parametrize(
    "analysis",
    [
        {"status": "ready", "data": None, "error": None},
        {"status": "unavailable", "data": {"x": 1}, "error": public_error("INTERNAL_ERROR").wire()},
        {"status": "unavailable", "data": None, "error": None},
        {"status": "ready", "data": {"score": float("nan")}, "error": None},
        {"status": "ready", "data": {"score": float("inf")}, "error": None},
    ],
)
def test_invalid_analysis_outcomes(analysis: dict[str, Any]) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        AnalysisOutcome.model_validate(analysis)


def test_invalid_snapshot_combinations() -> None:
    base = {
        "jobId": "job-1",
        "requestId": "req-1",
        "sessionId": "session-1",
        "createdAt": "2026-10-02T00:00:00.000Z",
        "callbackStatus": "pending",
    }
    with pytest.raises(ValueError, match="processing"):
        JobSnapshot.model_validate(
            {**base, "status": "processing", "finishedAt": None, "result": callback("ready", result()), "error": None}
        )
    with pytest.raises(ValueError, match="failed snapshot"):
        JobSnapshot.model_validate(
            {
                **base,
                "status": "failed",
                "finishedAt": "2026-10-02T00:01:00.000Z",
                "result": callback("failed", None, public_error("INTERNAL_ERROR").wire()),
                "error": None,
            }
        )
    assert VideoResult.model_validate(result()).duration_ms == 4000
