from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.core.common.video.dto import JobSnapshot, TerminalCallback
from app.core.common.video.errors import MESSAGES, RETRYABLE
from app.core.common.video.policy import VideoLimits
from app.inbound.http.video.dto import VideoAnalysisRequest
from app.main.config import CallbackSecuritySettings
from app.main.video_config import VideoSettings

DOCS = Path(__file__).resolve().parents[2] / "docs"
EXAMPLE = re.compile(r"<!-- example: ([a-z-]+) -->\n```json\n(.*?)\n```", re.DOTALL)


def examples() -> dict[str, Any]:
    text = (DOCS / "video-api.md").read_text()
    return {name: json.loads(body) for name, body in EXAMPLE.findall(text)}


def test_examples_and_settings_match_code() -> None:
    found = examples()
    assert set(found) == {
        "request",
        "accepted",
        "callback-unconfigured",
        "callback-file-error",
        "callback-failed",
        "snapshot-processing",
        "snapshot-failed",
    }
    VideoAnalysisRequest.model_validate(found["request"])
    assert set(found["accepted"]) == {"jobId", "requestId", "sessionId", "status"}
    for name in ("callback-unconfigured", "callback-file-error", "callback-failed"):
        assert TerminalCallback.model_validate(found[name]).wire() == found[name]
    for name in ("snapshot-processing", "snapshot-failed"):
        assert JobSnapshot.model_validate(found[name]).wire() == found[name]
    api = (DOCS / "video-api.md").read_text()
    for code, retryable in RETRYABLE.items():
        assert re.search(rf"\| `{code}` \| {str(retryable).lower()} \|", api), code
    operations = (DOCS / "video-analysis.md").read_text()
    for key in VideoSettings.model_fields:
        if key not in {"policy", "stage_retry_delays_seconds", "callback_retry_delays_seconds"}:
            assert f"VIDEO_{key.upper()}" in operations, key
    for key in VideoLimits.model_fields:
        assert key.upper() in operations, key
    for key in CallbackSecuritySettings.model_fields:
        assert f"APP_{key}" in operations, key


def test_failure_wrapper_and_unconfigured_examples_are_honest() -> None:
    found = examples()
    unconfigured = found["callback-unconfigured"]
    assert unconfigured["status"] == "unavailable"
    assert unconfigured["result"]["analysis"]["data"] is None
    assert unconfigured["result"]["analysis"]["error"]["code"] == "ANALYZER_NOT_CONFIGURED"
    assert unconfigured["result"]["error"] is None
    assert unconfigured["result"]["analysis"]["error"]["message"] == MESSAGES["ANALYZER_NOT_CONFIGURED"]
    failed = found["snapshot-failed"]
    assert failed["result"] is not None
    assert failed["result"]["result"] is None
    assert failed["error"] == failed["result"]["error"]
    combined = (DOCS / "video-api.md").read_text() + (DOCS / "video-analysis.md").read_text()
    assert '"status": "ready"' not in combined  # no example pretends the unconfigured service succeeds
    for forbidden in ("VIDEO_FAKE", "FAKE_ANALYZER"):
        assert forbidden not in combined
    for retention in ("종료 후 90일", "그 뒤 90일", "24시간"):
        assert retention in combined
