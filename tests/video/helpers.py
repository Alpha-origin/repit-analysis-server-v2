"""Test-only video doubles. Nothing in ``src/`` may import this module."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from pydantic import SecretStr

from app.core.common.video.dto import AnalysisOutcome, PreparedVideo, VideoRequest, VideoSource, public_error
from app.core.common.video.errors import VideoStageError
from app.core.common.video.identity import request_fingerprint
from app.core.common.video.policy import VideoLimits
from app.core.common.video.ports import Admit, ArtifactReservation
from app.main.config import CallbackSecuritySettings
from app.main.video_bootstrap import video_admission
from app.main.video_config import VideoSettings
from app.outbound.adapters.runtime_security_provider import StaticRuntimeSecurityProvider
from app.outbound.adapters.video.sqlite_repository import SqliteVideoRepository

SOURCE_HOST = "interview-media.s3.ap-northeast-2.amazonaws.com"
CALLBACK_HOST = "api.repit.example.com"
CALLBACK_URL = f"https://{CALLBACK_HOST}/api/analyses/video/callback"
VIDEO_TOKEN = "video-api-token"
CALLBACK_TOKEN = "callback-token"
SIGNED = (
    "X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIDEXAMPLE%2F20261002%2Fap-northeast-2%2Fs3%2Faws4_request"
    "&X-Amz-Date=20261002T000000Z&X-Amz-Expires=900&X-Amz-SignedHeaders=host&X-Amz-Signature={signature}"
)
START = 1_800_000_000.0


def file_url(signature: str = "first", extra: str = "") -> str:
    return f"https://{SOURCE_HOST}/videos/u7/interview.webm?{SIGNED.format(signature=signature)}{extra}"


def payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "requestId": "req-1",
        "sessionId": "session-1",
        "interviewId": "42",
        "userId": "7",
        "callbackUrl": CALLBACK_URL,
        "video": {
            "videoId": "video-1",
            "fileUrl": file_url(),
            "contentType": "video/webm",
            "fileSize": 6,
            "uploadedAt": "2026-10-02T09:00:00+09:00",
        },
    }
    video = overrides.pop("video", None)
    body.update(overrides)
    if video:
        body["video"] = {**body["video"], **video}
    return body


def video_request(**overrides: Any) -> VideoRequest:
    body = payload(**overrides)
    return VideoRequest(
        request_id=body["requestId"],
        session_id=body["sessionId"],
        interview_id=body["interviewId"],
        user_id=body["userId"],
        callback_url=body["callbackUrl"],
        video=VideoSource.model_validate(body["video"]),
    )


class FakeClock:
    def __init__(self, now: float = START) -> None:
        self.value = now

    def now(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def video_settings(tmp_path: Path, **overrides: Any) -> VideoSettings:
    values: dict[str, Any] = {
        "enabled": True,
        "api_token": SecretStr(VIDEO_TOKEN),
        "database_path": tmp_path / "video.sqlite3",
        "artifact_root": tmp_path / "artifacts",
        "policy": VideoLimits(source_hosts=(SOURCE_HOST,), min_free_disk_bytes=0),
        **overrides,
    }
    return VideoSettings(**values)


def callback_settings(**overrides: Any) -> CallbackSecuritySettings:
    values: dict[str, Any] = {
        "ENVIRONMENT": "test",
        "INTERNAL_CALLBACK_TOKEN": SecretStr(CALLBACK_TOKEN),
        "CALLBACK_ALLOWED_HOSTS": (CALLBACK_HOST,),
        **overrides,
    }
    return CallbackSecuritySettings(**values)


def security(**overrides: Any) -> StaticRuntimeSecurityProvider:
    values: dict[str, Any] = {
        "environment": "test",
        "callback_token": CALLBACK_TOKEN,
        "allowed_callback_hosts": (CALLBACK_HOST,),
        **overrides,
    }
    return StaticRuntimeSecurityProvider(**values)


class SwappableSecurity:
    """Stands in for a process restart with new credentials/blocks."""

    def __init__(self, provider: StaticRuntimeSecurityProvider) -> None:
        self.provider = provider

    def current(self) -> Any:
        return self.provider.current()


def repository(tmp_path: Path, clock: FakeClock | None = None) -> SqliteVideoRepository:
    return SqliteVideoRepository(tmp_path / "video.sqlite3", tmp_path / "artifacts", clock or FakeClock())


def admit_for(tmp_path: Path, provider: Any | None = None, **overrides: Any) -> Admit:
    return video_admission(video_settings(tmp_path, **overrides), provider or security())


def submit(repo: SqliteVideoRepository, tmp_path: Path, request: VideoRequest | None = None) -> str:
    request = request or video_request()
    return repo.submit(request, request_fingerprint(request), admit_for(tmp_path)).job_id


class FakeVideoAnalyzer:
    """Test-only analyzer. Production code binds UnconfiguredVideoAnalyzer exclusively."""

    def __init__(self, outcome: AnalysisOutcome | None = None, error: Exception | None = None) -> None:
        self.outcome = outcome or AnalysisOutcome(status="ready", data={"segments": [{"startMs": 0}]})
        self.error = error
        self.calls: list[PreparedVideo] = []

    async def analyze(self, prepared: PreparedVideo) -> AnalysisOutcome:
        self.calls.append(prepared)
        if self.error is not None:
            raise self.error
        return self.outcome


def partial_outcome() -> AnalysisOutcome:
    return AnalysisOutcome(status="partial", data={"segments": []}, error=public_error("EXTERNAL_SERVICE_UNAVAILABLE"))


@dataclass
class FakeMedia:
    """Deterministic media backend: writes bytes on download and returns canned metadata."""

    fail: dict[str, VideoStageError | Exception] = field(default_factory=dict)
    calls: Counter[str] = field(default_factory=Counter)
    content: bytes = b"video!"
    duration_ms: int = 4_000
    hold: Any = None  # optional asyncio.Event awaited by inspect (to test cancellation)

    async def download(
        self, request: VideoRequest, limits: VideoLimits, reservation: ArtifactReservation
    ) -> dict[str, Any]:
        self.calls["source"] += 1
        if "source" in self.fail:
            raise self.fail["source"]
        Path(reservation.temp_path).write_bytes(self.content)
        return {"path": reservation.final_path, "sha256": "0" * 64, "bytes": len(self.content)}

    async def inspect(self, path: str, limits: VideoLimits) -> dict[str, Any]:
        self.calls["inspect"] += 1
        if self.hold is not None:
            await self.hold.wait()
        if "inspect" in self.fail:
            raise self.fail["inspect"]
        return {
            "container": "webm",
            "codec": "vp9",
            "streamIndex": 0,
            "codedWidth": 1280,
            "codedHeight": 720,
            "sampleAspectRatio": "1",
            "rotationDegrees": 0.0,
            "displayLongEdge": 1280,
            "displayShortEdge": 720,
            "timeBase": "1/1000",
            "declaredDuration": None,
        }

    async def validate(self, path: str, inspected: dict[str, Any], limits: VideoLimits) -> dict[str, Any]:
        self.calls["validate"] += 1
        if "validate" in self.fail:
            raise self.fail["validate"]
        return {"durationMs": self.duration_ms, "frameCount": 120, "averageFps": 30.0}


@dataclass
class Receiver:
    """Test-only API receiver: checks the token and records (jobId, requestId) bodies it accepts."""

    token: str = CALLBACK_TOKEN
    statuses: list[int] = field(default_factory=list)
    requests: list[httpx.Request] = field(default_factory=list)
    stored: dict[str, dict[str, Any]] = field(default_factory=dict)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("x-internal-token") != self.token:
            return httpx.Response(401)
        status = self.statuses.pop(0) if self.statuses else 200
        if 200 <= status < 300:
            body = json.loads(request.content)
            latest = self.stored.get(body["requestId"])
            # Only the job it asked for is kept; duplicates are idempotent.
            if latest is None or latest["jobId"] == body["jobId"]:
                self.stored[body["requestId"]] = body
        return httpx.Response(status)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


async def drain(step: Callable[[], Any], limit: int = 50) -> int:
    for count in range(limit):
        if not await step():
            return count
    raise AssertionError("worker did not become idle")
