from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

from app.core.commands.process_video_task import ProcessVideoTask
from app.core.common.video.dto import PreparedVideo
from app.core.common.video.errors import VideoStageError
from app.core.common.video.identity import request_fingerprint
from app.core.common.video.policy import VideoLimits
from app.outbound.adapters.video import process_analyzer as process_analyzer_module
from app.outbound.adapters.video.media_process import ProcessLimits, ProcessResult
from app.outbound.adapters.video.process_analyzer import ProcessVideoAnalyzer
from tests.video.helpers import SOURCE_HOST, FakeMedia, admit_for, drain, repository, video_request

SCRIPT = """
import argparse, json, os, time
from pathlib import Path
parser = argparse.ArgumentParser()
parser.add_argument('--model-path')
parser.add_argument('--model-version')
parser.add_argument('--input-json')
args = parser.parse_args()
prepared = json.loads(args.input_json)
Path(prepared['path'] + '.pid').write_text(str(os.getpid()))
mode = Path(args.model_path).read_text()
if mode == 'stall': time.sleep(60)
data = {'schemaVersion': 'behavior-v1', 'modelVersion': args.model_version, 'segments': []}
if mode == 'bad-version': data['schemaVersion'] = 'unsupported'
if mode == 'malformed': print('not-json')
else: print(json.dumps({'status': 'ready', 'data': data, 'error': None}))
"""


def analyzer(tmp_path: Path, mode: str) -> ProcessVideoAnalyzer:
    executable = tmp_path / "analyzer"
    executable.write_text(f"#!{sys.executable}\n{SCRIPT}")
    executable.chmod(0o700)
    model = tmp_path / "model.bin"
    model.write_text(mode)
    return ProcessVideoAnalyzer(executable, model, "model-v1", "behavior-v1")


LIMITS = VideoLimits(source_hosts=(SOURCE_HOST,), max_process_rss_bytes=512 * 1024 * 1024)


def prepared(tmp_path: Path) -> PreparedVideo:
    return PreparedVideo(
        video_id="v",
        path=str(tmp_path / "video"),
        sha256="0" * 64,
        bytes=6,
        container="webm",
        codec="vp9",
        display_long_edge=640,
        display_short_edge=480,
        rotation_degrees=0,
        frame_count=30,
        duration_ms=1000,
        average_fps=30,
    )


@pytest.mark.asyncio
async def test_local_executable_contract_and_versions(tmp_path: Path) -> None:
    outcome = await analyzer(tmp_path, "ready").analyze(prepared(tmp_path), LIMITS)
    assert outcome.status == "ready"
    assert isinstance(outcome.data, dict)
    assert outcome.data["modelVersion"] == "model-v1"
    assert outcome.data["schemaVersion"] == "behavior-v1"


@pytest.mark.parametrize("mode", ["bad-version", "malformed"])
@pytest.mark.asyncio
async def test_invalid_model_output_is_public_internal_error(tmp_path: Path, mode: str) -> None:
    with pytest.raises(VideoStageError) as caught:
        await analyzer(tmp_path, mode).analyze(prepared(tmp_path), LIMITS)
    assert caught.value.code == "INTERNAL_ERROR"


@pytest.mark.asyncio
async def test_stage_timeout_kills_and_reaps_actual_analyzer(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    request = video_request()
    job = repo.submit(
        request,
        request_fingerprint(request),
        admit_for(
            tmp_path,
            policy=VideoLimits(source_hosts=(SOURCE_HOST,), min_free_disk_bytes=0, analyze_timeout_seconds=1),
            stage_max_attempts=1,
            stage_retry_delays_seconds=(),
        ),
    ).job_id
    await drain(ProcessVideoTask(repo, FakeMedia(), analyzer(tmp_path, "stall"), {"io": 1, "cpu": 1}).run_once)
    source = json.loads(repo.task_rows(job)["source"]["result"])["path"]
    pid = int(Path(source + ".pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    with sqlite3.connect(repo.path) as connection:
        body = json.loads(connection.execute("SELECT body FROM video_results").fetchone()[0])
    assert body["result"]["analysis"]["error"]["code"] == "PROCESSING_TIMEOUT"
    assert body["result"]["error"] is None


@pytest.mark.asyncio
async def test_child_limits_follow_the_accepted_policy_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 워커가 뜰 때의 설정이 아니라 작업마다 넘어온 정책 값으로 자식 프로세스를 제한한다.
    captured: list[ProcessLimits] = []

    async def fake_run_bounded(program: str, args: list[str], limits: ProcessLimits, **_: object) -> ProcessResult:
        captured.append(limits)
        data = {"schemaVersion": "behavior-v1", "modelVersion": "model-v1"}
        stdout = json.dumps({"status": "ready", "data": data, "error": None}).encode()
        return ProcessResult(returncode=0, stdout=stdout, stdout_bytes=len(stdout), stderr_bytes=0)

    monkeypatch.setattr(process_analyzer_module, "run_bounded", fake_run_bounded)
    snapshot = VideoLimits(
        source_hosts=(SOURCE_HOST,), analyze_timeout_seconds=172_800, max_process_rss_bytes=3 * 1024 * 1024 * 1024
    )
    await analyzer(tmp_path, "ready").analyze(prepared(tmp_path), snapshot)
    assert captured[0].timeout_seconds == 172_800
    assert captured[0].max_rss_bytes == 3 * 1024 * 1024 * 1024


@pytest.mark.asyncio
async def test_child_timeout_is_retried_like_the_stage_timeout(tmp_path: Path) -> None:
    snapshot = VideoLimits(source_hosts=(SOURCE_HOST,), analyze_timeout_seconds=1)
    with pytest.raises(VideoStageError) as caught:
        await analyzer(tmp_path, "stall").analyze(prepared(tmp_path), snapshot)
    assert caught.value.code == "PROCESSING_TIMEOUT"
    assert caught.value.stage_retry is True
