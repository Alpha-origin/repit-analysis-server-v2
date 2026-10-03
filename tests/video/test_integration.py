"""Real app + SQLite + processor + outbox, with a test-only receiver. Not a production API-server integration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.common.video.dto import AnalysisOutcome
from app.main.config import AnthropicSettings
from app.main.run import make_app
from app.main.video_worker import VideoWorker, build_worker
from tests.video.helpers import (
    VIDEO_TOKEN,
    FakeMedia,
    FakeVideoAnalyzer,
    Receiver,
    callback_settings,
    drain,
    partial_outcome,
    payload,
    repository,
    video_settings,
)

AUTH = {"X-Internal-Token": VIDEO_TOKEN}


class Harness:
    def __init__(self, tmp_path: Path, receiver: Receiver, analyzer: Any = None, media: FakeMedia | None = None):
        self.settings = video_settings(tmp_path)
        self.app = make_app(
            anthropic_settings=AnthropicSettings(API_KEY="test"),
            callback_security_settings=callback_settings(),
            video_settings=self.settings,
        )
        self.worker: VideoWorker = build_worker(
            callback_settings=callback_settings(),
            video_settings=self.settings,
            analyzer=analyzer,
            media=media or FakeMedia(),
            callback_transport=receiver.transport(),
        )
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")

    async def post(self, body: dict[str, Any]) -> httpx.Response:
        return await self.http.post("/analysis/video", json=body, headers=AUTH)

    async def get(self, job_id: str) -> dict[str, Any]:
        response = await self.http.get(f"/analysis/video/jobs/{job_id}", headers=AUTH)
        assert response.status_code == 200
        return response.json()  # type: ignore[no-any-return]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("analyzer", "expected"),
    [
        (FakeVideoAnalyzer(), "ready"),
        (FakeVideoAnalyzer(partial_outcome()), "partial"),
        (None, "unavailable"),  # the real unconfigured analyzer
    ],
)
async def test_terminal_contracts_and_lookup_recovery(tmp_path: Path, analyzer: Any, expected: str) -> None:
    receiver = Receiver()
    harness = Harness(tmp_path, receiver, analyzer)
    accepted = await harness.post(payload())
    job_id = accepted.json()["jobId"]
    await drain(harness.worker.run_once)
    assert len(receiver.requests) == 1
    delivered = json.loads(receiver.requests[0].content)
    snapshot = await harness.get(job_id)
    assert snapshot["result"] == delivered  # callback body and GET.result are the same document
    assert (snapshot["status"], snapshot["callbackStatus"], snapshot["error"]) == ("completed", "delivered", None)
    assert delivered["status"] == expected
    assert delivered["result"]["status"] == expected
    if analyzer is None:
        assert delivered["result"]["analysis"]["error"]["code"] == "ANALYZER_NOT_CONFIGURED"
        assert delivered["result"]["error"] is None
    await harness.http.aclose()


@pytest.mark.asyncio
async def test_failed_document_and_failed_delivery_recover_through_get(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: Any) -> Any:
        raise RuntimeError("generation failed")

    monkeypatch.setattr("app.core.commands.process_video_task.build_terminal_callback", broken)
    receiver = Receiver(statuses=[400])
    harness = Harness(tmp_path, receiver)
    job_id = (await harness.post(payload())).json()["jobId"]
    await drain(harness.worker.run_once)
    snapshot = await harness.get(job_id)
    assert snapshot["status"] == "failed"
    assert snapshot["callbackStatus"] == "failed"
    assert snapshot["result"] is not None
    assert snapshot["result"]["status"] == "failed"
    assert snapshot["result"]["result"] is None
    assert snapshot["error"] == snapshot["result"]["error"]
    assert snapshot["result"] == json.loads(receiver.requests[0].content)
    await harness.http.aclose()


@pytest.mark.asyncio
async def test_receiver_rejects_bad_token_stale_and_mismatched_callbacks(tmp_path: Path) -> None:
    receiver = Receiver(token="receiver-expects-another-token")
    analyzer = FakeVideoAnalyzer()
    harness = Harness(tmp_path, receiver, analyzer)
    job_id = (await harness.post(payload())).json()["jobId"]
    await drain(harness.worker.run_once)
    assert receiver.stored == {}
    assert len(receiver.requests) == 1  # 401 is a permanent delivery failure ...
    assert len(analyzer.calls) == 1  # ... and never triggers a fresh analysis
    snapshot = await harness.get(job_id)
    assert (snapshot["status"], snapshot["callbackStatus"]) == ("completed", "failed")
    # The receiver keeps only the job it is waiting for; stale/mismatched/duplicate deliveries are ignored.
    good = Receiver()
    good.stored["req-1"] = {"jobId": job_id, "requestId": "req-1"}
    stale = {**snapshot["result"], "jobId": "some-older-job"}
    good.handler(httpx.Request("POST", "https://x", json=stale, headers={"X-Internal-Token": good.token}))
    good.handler(httpx.Request("POST", "https://x", json=snapshot["result"], headers={"X-Internal-Token": good.token}))
    good.handler(httpx.Request("POST", "https://x", json=snapshot["result"], headers={"X-Internal-Token": good.token}))
    assert good.stored["req-1"]["jobId"] == job_id
    await harness.http.aclose()


@pytest.mark.asyncio
async def test_lost_acceptance_response_recovers_same_job(tmp_path: Path) -> None:
    receiver = Receiver()
    analyzer = FakeVideoAnalyzer(AnalysisOutcome(status="ready", data={"ok": True}))
    harness = Harness(tmp_path, receiver, analyzer)
    first = await harness.post(payload())  # pretend this 202 was lost on the way back
    second = await harness.post(payload(video={"fileUrl": payload()["video"]["fileUrl"].replace("first", "x")}))
    assert first.json()["jobId"] == second.json()["jobId"]
    repo = repository(tmp_path)
    assert len(repo.task_rows(first.json()["jobId"])) == 5  # one DAG
    await drain(harness.worker.run_once)
    third = await harness.post(payload())  # retried again after completion: still the same job
    assert third.json()["jobId"] == first.json()["jobId"]
    assert len(analyzer.calls) == 1
    assert len(receiver.requests) == 1
    await harness.http.aclose()
