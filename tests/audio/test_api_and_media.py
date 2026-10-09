from __future__ import annotations

import hashlib
import shutil
import struct
import wave
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from app.core.common.audio.dto import AudioAnswer, AudioError, AudioPolicy
from app.inbound.http.audio.router import make_audio_router
from app.main.audio_config import AudioSettings
from app.main.config import AnthropicSettings
from app.main.run import make_app
from app.outbound.adapters.audio.media_backend import LocalAudioBackend, inside
from app.outbound.adapters.audio.sqlite_repository import SqliteAudioRepository
from tests.audio.helpers import request


@pytest.mark.asyncio
async def test_api_accepts_once_rejects_conflict_and_invalid_callback(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(
        make_audio_router(SqliteAudioRepository(tmp_path / "jobs.db"), AudioPolicy(), ["hooks.example.test"])
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/audio/analysis", json=request().model_dump(by_alias=True))
        assert response.status_code == 202
        job_id = response.json()["jobId"]
        duplicate = await client.post("/audio/analysis", json=request().model_dump(by_alias=True))
        assert duplicate.json()["jobId"] == job_id
        conflict = await client.post("/audio/analysis", json=request(2).model_dump(by_alias=True))
        assert conflict.status_code == 409
        invalid = request().model_dump(by_alias=True)
        invalid["callbackUrl"] = "https://hooks.example.test@127.0.0.1/result"
        rejected = await client.post("/audio/analysis", json=invalid)
        assert rejected.status_code == 422
        status = await client.get(f"/audio/jobs/{job_id}")
        assert status.json()["status"] == "processing"
        assert "answerIndex" in status.json()["tasks"][0]
        missing = await client.get("/audio/jobs/unknown")
        assert missing.status_code == 404


def test_audio_router_is_opt_in_and_wired_in_real_application(tmp_path: Path) -> None:
    disabled = make_app(
        anthropic_settings=AnthropicSettings(API_KEY="test"), audio_settings=AudioSettings(enabled=False)
    )
    assert "/audio/analysis" not in {getattr(route, "path", "") for route in disabled.routes}
    enabled = make_app(
        anthropic_settings=AnthropicSettings(API_KEY="test"),
        audio_settings=AudioSettings(enabled=True, database_path=tmp_path / "jobs.db"),
    )
    assert "/audio/analysis" in {getattr(route, "path", "") for route in enabled.routes}


@pytest.mark.parametrize("key", ["../outside.wav", "/etc/passwd", "sub/../../outside"])
def test_asset_key_cannot_escape_upload_root(tmp_path: Path, key: str) -> None:
    with pytest.raises(AudioError, match="invalid_asset_key"):
        inside(tmp_path, key)


def test_symlink_cannot_escape_upload_root(tmp_path: Path) -> None:
    root = tmp_path / "uploads"
    root.mkdir()
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"outside")
    (root / "link.wav").symlink_to(outside)
    with pytest.raises(AudioError, match="invalid_asset_key"):
        inside(root, "link.wav")


@pytest.mark.asyncio
async def test_checksum_mismatch_leaves_no_completed_or_temporary_artifact(tmp_path: Path) -> None:
    source = tmp_path / "input"
    source.mkdir()
    (source / "answer.wav").write_bytes(b"not-the-requested-content")
    output = tmp_path / "output"
    backend = LocalAudioBackend(source, output)
    answer = AudioAnswer(answer_id="a", question_id="q", asset_key="answer.wav", sha256="0" * 64)
    with pytest.raises(AudioError, match="checksum_mismatch"):
        await backend.execute("source", answer, AudioPolicy(), {})
    assert list(output.iterdir()) == []


def write_wave(path: Path) -> None:
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(struct.pack("<640h", *([0] * 320 + [32767] * 320)))


@pytest.mark.asyncio
async def test_quality_preserves_zero_and_clipping_evidence(tmp_path: Path) -> None:
    path = tmp_path / "audio.wav"
    write_wave(path)
    backend = LocalAudioBackend(tmp_path, tmp_path / "output")
    quality = await backend.execute("quality", request().answers[0], AudioPolicy(), {"normalize": {"path": str(path)}})
    assert quality["clipping_suspect_ratio"] == 0.5
    assert quality["frames"][0]["all_zero"] is True
    assert quality["frames"][1]["rms"] > 0.99


@pytest.mark.asyncio
@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg binaries not installed")
async def test_real_ffmpeg_preserves_duration_and_silence(tmp_path: Path) -> None:
    path = tmp_path / "audio.wav"
    write_wave(path)
    answer = AudioAnswer(
        answer_id="a", question_id="q", asset_key="audio.wav", sha256=hashlib.sha256(path.read_bytes()).hexdigest()
    )
    backend = LocalAudioBackend(tmp_path, tmp_path / "output")
    policy = AudioPolicy()
    source = await backend.execute("source", answer, policy, {})
    inspected = await backend.execute("inspect", answer, policy, {"source": source})
    normalized = await backend.execute("normalize", answer, policy, {"source": source, "inspect": inspected})
    assert normalized["sample_count"] == 640
    assert normalized["duration_ms"] == 40
    quality = await backend.execute("quality", answer, policy, {"normalize": normalized})
    assert quality["frames"][0]["all_zero"] is True
