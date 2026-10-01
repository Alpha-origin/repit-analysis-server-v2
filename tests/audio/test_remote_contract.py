from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.core.commands.process_audio_task import ProcessAudioTask
from app.core.common.audio.dto import AudioAnswer, AudioError, AudioPolicy
from app.inbound.http.audio.dto import AudioAnalysisRequest
from app.inbound.http.audio.router import make_audio_router
from app.outbound.adapters.audio.media_backend import LocalAudioBackend
from app.outbound.adapters.audio.sqlite_repository import SqliteAudioRepository
from tests.audio.helpers import FakeBackend, FakeClient, FakeWebhook

HOST = "test-bucket.s3.ap-northeast-2.amazonaws.com"


def payload() -> dict[str, Any]:
    return {
        "requestId": "r1",
        "sessionId": "s1",
        "interviewId": "42",
        "userId": "7",
        "callbackUrl": "https://api.example.com/api/analyses/audio/callback",
        "recordings": [
            {
                "recordingId": "301",
                "questionId": "101",
                "answerId": "201",
                "fileUrl": f"https://{HOST}/recording.mp3?X-Amz-Signature=first",
                "contentType": "audio/mpeg",
                "fileSize": 6,
                "uploadedAt": "2026-09-21T07:56:31Z",
                "endReason": "user",
            }
        ],
    }


@pytest.mark.asyncio
async def test_remote_request_identity_validation_and_callback(tmp_path: Path) -> None:
    repository = SqliteAudioRepository(tmp_path / "jobs.db")
    policy = AudioPolicy(source_hosts=(HOST,))
    app = FastAPI()
    app.include_router(make_audio_router(repository, policy, ["api.example.com"]))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/analysis/audio", json=payload())
        assert response.status_code == 202
        job_id = response.json()["jobId"]
        assert response.json()["requestId"] == "r1"
        renewed = payload()
        renewed["recordings"][0]["fileUrl"] = f"https://{HOST}/recording.mp3?X-Amz-Signature=second"
        assert (await client.post("/analysis/audio", json=renewed)).json()["jobId"] == job_id
        renewed["recordings"][0]["fileUrl"] += "&versionId=different"
        assert (await client.post("/analysis/audio", json=renewed)).status_code == 409
        invalid = payload()
        invalid["recordings"] *= 2
        assert (await client.post("/analysis/audio", json=invalid)).status_code == 422
        invalid = payload()
        invalid["recordings"][0]["fileUrl"] = "https://127.0.0.1/private"
        assert (await client.post("/analysis/audio", json=invalid)).status_code == 422
        invalid = payload()
        invalid.pop("callbackUrl")
        assert (await client.post("/analysis/audio", json=invalid)).status_code == 422
        webhook = FakeWebhook()
        processor = ProcessAudioTask(
            repository, FakeBackend(), FakeClient(), webhook, {"cpu": 2, "inference": 1, "llm": 1, "callback": 1}
        )
        for _ in range(30):
            if not await processor.run_once():
                break
        assert webhook.calls == 1
        result = webhook.payloads[0]
        assert result["requestId"] == "r1"
        assert result["interviewId"] == "42"
        assert result["status"] == "partial"
        assert result["results"][0]["recordingId"] == "301"
        assert result["results"][0]["timing"]["data"]["speed"] is not None
        status = await client.get(f"/analysis/audio/jobs/{job_id}")
        assert status.json()["result"] == result
        assert "fileUrl" not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (200, b"abcdef", None),
        (200, b"abc", "source_size_mismatch"),
        (200, b"abcdefg", "source_too_large_or_size_mismatch"),
        (403, b"", "source_access_denied_or_expired"),
        (302, b"", "source_download_failed"),
        (503, b"", "source_download_failed"),
    ],
)
async def test_download_integrity_limits_and_cleanup(
    tmp_path: Path,
    status: int,
    body: bytes,
    code: str | None,
) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, content=body, headers={"location": "https://127.0.0.1/private"})

    backend = LocalAudioBackend(tmp_path, tmp_path / "artifacts", httpx.MockTransport(respond))
    answer = AudioAnalysisRequest.model_validate(payload()).to_job().answers[0]
    if code:
        with pytest.raises(AudioError, match=code) as error:
            await backend.execute("source", answer, AudioPolicy(source_hosts=(HOST,)), {})
        assert error.value.retryable == (status == 503)
        assert not list((tmp_path / "artifacts").iterdir())
    else:
        result = await backend.execute("source", answer, AudioPolicy(source_hosts=(HOST,)), {})
        assert result["sha256"] == hashlib.sha256(body).hexdigest()
        assert await asyncio.to_thread(Path(result["path"]).read_bytes) == body
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_source_failure_is_in_callback_without_hiding_other_answers(tmp_path: Path) -> None:
    class PartialBackend(FakeBackend):
        async def execute(
            self, stage: str, answer: AudioAnswer, policy: AudioPolicy, inputs: dict[str, dict[str, Any]]
        ) -> dict[str, Any]:
            if stage == "source" and answer.recording_id == "301":
                raise AudioError("source_access_denied_or_expired")
            return await super().execute(stage, answer, policy, inputs)

    data = payload()
    data["recordings"].append({**data["recordings"][0], "recordingId": "302", "answerId": "202"})
    repository = SqliteAudioRepository(tmp_path / "jobs.db")
    repository.submit(AudioAnalysisRequest.model_validate(data).to_job(), AudioPolicy())
    webhook = FakeWebhook()
    processor = ProcessAudioTask(
        repository, PartialBackend(), FakeClient(), webhook, {"cpu": 2, "inference": 1, "llm": 1, "callback": 1}
    )
    for _ in range(40):
        if not await processor.run_once():
            break
    result = webhook.payloads[0]
    assert result["status"] == "partial"
    assert result["results"][0]["status"] == "unavailable"
    assert result["results"][0]["error"]["code"] == "SOURCE_ACCESS_DENIED_OR_EXPIRED"
    assert result["results"][1]["timing"]["status"] == "ready"
