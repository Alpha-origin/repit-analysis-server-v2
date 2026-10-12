from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from app.inbound.http.video.router import make_video_router
from app.main.config import AnthropicSettings
from app.main.run import make_app
from tests.video.helpers import VIDEO_TOKEN, callback_settings, file_url, payload, video_settings

AUTH = {"X-Internal-Token": VIDEO_TOKEN}


def app_for(tmp_path: Path, **video: Any) -> FastAPI:
    return make_app(
        anthropic_settings=AnthropicSettings(API_KEY="test"),
        callback_security_settings=callback_settings(),
        video_settings=video_settings(tmp_path, **video),
    )


def client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_authenticated_accept_and_lookup(tmp_path: Path) -> None:
    async with client(app_for(tmp_path)) as http:
        accepted = await http.post("/analysis/video", json=payload(), headers=AUTH)
        assert accepted.status_code == 202
        body = accepted.json()
        assert set(body) == {"jobId", "requestId", "sessionId", "status"}
        assert (body["requestId"], body["sessionId"], body["status"]) == ("req-1", "session-1", "accepted")
        # Lost 202: the identical (re-signed) request returns the same job.
        resigned = payload(video={"fileUrl": file_url("again")})
        replay = await http.post("/analysis/video", json=resigned, headers=AUTH)
        assert (replay.status_code, replay.json()["jobId"]) == (202, body["jobId"])
        conflict = await http.post("/analysis/video", json=payload(userId="8"), headers=AUTH)
        assert conflict.status_code == 409
        lookup = await http.get(f"/analysis/video/jobs/{body['jobId']}", headers=AUTH)
        assert lookup.status_code == 200
        snapshot = lookup.json()
        assert snapshot["status"] == "processing"
        assert set(snapshot) == {
            "jobId",
            "requestId",
            "sessionId",
            "status",
            "createdAt",
            "finishedAt",
            "callbackStatus",
            "result",
            "error",
        }
        missing = await http.get("/analysis/video/jobs/unknown", headers=AUTH)
        assert (missing.status_code, missing.json()["code"]) == (404, "JOB_NOT_FOUND")


class SpyRepository:
    def __init__(self) -> None:
        self.calls = 0

    def submit(self, *args: Any) -> Any:
        self.calls += 1
        raise AssertionError("must not be reached")

    def snapshot(self, job_id: str) -> Any:
        self.calls += 1
        raise AssertionError("must not be reached")


@pytest.mark.asyncio
async def test_auth_precedes_body_and_job_lookup(tmp_path: Path) -> None:
    spy = SpyRepository()
    app = FastAPI()
    app.include_router(make_video_router(spy, lambda request: None, SecretStr(VIDEO_TOKEN)))  # type: ignore[arg-type, return-value]
    async with client(app) as http:
        for headers in ({}, {"X-Internal-Token": "wrong"}, {"X-Internal-Token": ""}):
            invalid_json = await http.post(
                "/analysis/video", content=b"{not json", headers={**headers, "Content-Type": "application/json"}
            )
            assert invalid_json.status_code == 401
            huge = await http.post("/analysis/video", content=b"x" * 200_000, headers=headers)
            assert huge.status_code == 401
            known = await http.get("/analysis/video/jobs/real-looking-id", headers=headers)
            unknown = await http.get("/analysis/video/jobs/unknown", headers=headers)
            assert known.status_code == unknown.status_code == 401
            assert known.json() == unknown.json()
    assert spy.calls == 0


@pytest.mark.asyncio
async def test_video_routes_opt_in_and_safe_errors(tmp_path: Path) -> None:
    disabled = make_app(
        anthropic_settings=AnthropicSettings(API_KEY="test"),
        callback_security_settings=callback_settings(),
        video_settings=video_settings(tmp_path, enabled=False),
    )
    assert "/analysis/video" not in {getattr(route, "path", "") for route in disabled.routes}
    async with client(app_for(tmp_path)) as http:
        bad_json = await http.post(
            "/analysis/video", content=b"{oops", headers={**AUTH, "Content-Type": "application/json"}
        )
        assert (bad_json.status_code, bad_json.json()["code"]) == (422, "INVALID_JSON")
        too_large = await http.post("/analysis/video", content=b" " * 70_000, headers=AUTH)
        assert too_large.status_code == 413
        invalid = payload(video={"fileSize": "6"})
        rejected = await http.post("/analysis/video", json=invalid, headers=AUTH)
        assert rejected.status_code == 422
        assert "X-Amz-Signature" not in rejected.text
        assert rejected.json()["details"][0]["loc"] == ["video", "fileSize"]
        not_admitted = await http.post("/analysis/video", json=payload(video={"fileSize": 2_000_000_000}), headers=AUTH)
        assert (not_admitted.status_code, not_admitted.json()["details"]) == (422, [{"reason": "video_too_large"}])
        # Existing routes stay registered next to the opt-in video routes.
        paths = {getattr(route, "path", "") for route in app_for(tmp_path).routes}
        assert {"/generate", "/feedback/solo", "/questions/tailor", "/analysis/video/jobs/{job_id}"} <= paths
