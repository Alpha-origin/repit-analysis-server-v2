"""Real-surface QA: uvicorn HTTP + separate worker process + real FFprobe + production analyzer.

    PYTHONPATH=src .venv/bin/python -m tests.video.qa_scenario --evidence-dir <dir> --require-real-media
    ... --scenario callback-failure | expired-job

External S3 and the API-server receiver are controlled substitutes; this is NOT a production S3/API
integration test. Only dummy credentials and synthetic media are used.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx

from app.core.common.security.redaction import redact_text
from app.main.config import CallbackSecuritySettings
from app.main.video_cleanup import build_cleanup
from app.main.video_config import VideoSettings
from tests.video.media_fixtures import TOOLS_AVAILABLE, lavfi_video, remux
from tests.video.qa_support import OffsetClock

ROOT = Path(__file__).resolve().parents[2]
SOURCE_HOST = "qa-bucket.s3.ap-northeast-2.amazonaws.com"
CALLBACK_HOST = "api.qa.example.com"
VIDEO_TOKEN = "qa-video-token"
CALLBACK_TOKEN = "qa-callback-token"
DAY = 86_400


class QaFailureError(AssertionError):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise QaFailureError(message)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def environment(work: Path, mode: str) -> dict[str, str]:
    return {
        **os.environ,
        "PYTHONPATH": f"{ROOT / 'src'}{os.pathsep}{ROOT}",
        "ANTHROPIC_API_KEY": "qa-dummy",
        "APP_ENVIRONMENT": "production",
        "APP_INTERNAL_CALLBACK_TOKEN": CALLBACK_TOKEN,
        "APP_CALLBACK_ALLOWED_HOSTS": CALLBACK_HOST,
        "VIDEO_ENABLED": "true",
        "VIDEO_API_TOKEN": VIDEO_TOKEN,
        "VIDEO_DATABASE_PATH": str(work / "video.sqlite3"),
        "VIDEO_ARTIFACT_ROOT": str(work / "artifacts"),
        "VIDEO_POLICY__SOURCE_HOSTS": json.dumps([SOURCE_HOST]),
        "VIDEO_POLICY__MIN_FREE_DISK_BYTES": "0",
        "QA_FIXTURES": str(work / "fixtures"),
        "QA_RECEIVER_LOG": str(work / "receiver.jsonl"),
        "QA_RECEIVER_MODE": mode,
        "QA_CLOCK_FILE": str(work / "clock-offset"),
    }


@contextlib.contextmanager
def processes(work: Path, env: dict[str, str], port: int) -> Iterator[None]:
    logs = [(work / "api.log").open("w"), (work / "worker.log").open("w")]
    api = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tests.video.qa_support:create_app",
            "--factory",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=env,
        stdout=logs[0],
        stderr=subprocess.STDOUT,
    )
    worker = subprocess.Popen(
        [sys.executable, "-m", "tests.video.qa_support"], cwd=ROOT, env=env, stdout=logs[1], stderr=subprocess.STDOUT
    )
    try:
        yield
    finally:
        for process in (api, worker):
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for log in logs:
            log.close()


def wait_healthy(client: httpx.Client) -> None:
    for _ in range(100):
        with contextlib.suppress(httpx.HTTPError):
            if client.get("/health").status_code == 200:
                return
        time.sleep(0.1)
    raise QaFailureError("API did not become healthy")


def body(video_file: str, size: int, request_id: str, signature: str = "s1") -> dict[str, Any]:
    return {
        "requestId": request_id,
        "sessionId": "qa-session",
        "interviewId": "42",
        "userId": "7",
        "callbackUrl": f"https://{CALLBACK_HOST}/api/analyses/video/callback",
        "video": {
            "videoId": f"video-{request_id}",
            "fileUrl": f"https://{SOURCE_HOST}/qa/{video_file}?X-Amz-Algorithm=AWS4-HMAC-SHA256"
            f"&X-Amz-Signature={signature}",
            "contentType": "video/webm",
            "fileSize": size,
            "uploadedAt": "2026-10-02T09:00:00+09:00",
        },
    }


def wait_terminal(client: httpx.Client, job_ids: list[str], delivered: bool = True) -> dict[str, dict[str, Any]]:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        snapshots = {job: client.get(f"/analysis/video/jobs/{job}").json() for job in job_ids}
        done = all(item["status"] != "processing" for item in snapshots.values())
        settled = all(item["callbackStatus"] in ("delivered", "failed") for item in snapshots.values())
        if done and (settled or not delivered):
            return snapshots
        time.sleep(0.3)
    raise QaFailureError("jobs did not finish in time")


def receiver_entries(work: Path) -> list[dict[str, Any]]:
    path = work / "receiver.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def sanitize(value: Any) -> Any:
    return json.loads(redact_text(json.dumps(value, ensure_ascii=False)))


def prepare_media(work: Path) -> dict[str, int]:
    fixtures = work / "fixtures"
    fixtures.mkdir()
    lavfi_video(fixtures / "valid.webm", codec="libvpx-vp9", seconds=2, audio=True)
    mkv = remux(lavfi_video(work / "src.webm", codec="libvpx", seconds=1), work / "generic.mkv")
    shutil.copy(mkv, fixtures / "disguised.webm")  # MIME/extension claim webm, container is generic MKV
    return {path.name: path.stat().st_size for path in fixtures.iterdir()}


def scenario_default(client: httpx.Client, work: Path, sizes: dict[str, int], summary: dict[str, Any]) -> None:
    anonymous = httpx.post(f"{client.base_url}analysis/video", content=b"{bad", timeout=10)
    check(anonymous.status_code == 401, "missing token must be 401 before parsing")
    check(httpx.get(f"{client.base_url}analysis/video/jobs/x", timeout=10).status_code == 401, "GET auth")
    accepted = client.post("/analysis/video", json=body("valid.webm", sizes["valid.webm"], "qa-valid"))
    check(accepted.status_code == 202, f"valid accept {accepted.status_code}")
    replay = client.post("/analysis/video", json=body("valid.webm", sizes["valid.webm"], "qa-valid", "resigned"))
    check(replay.json()["jobId"] == accepted.json()["jobId"], "lost-202 replay must return the original job")
    mismatch = client.post("/analysis/video", json=body("valid.webm", sizes["valid.webm"] + 1, "qa-size"))
    disguised = client.post("/analysis/video", json=body("disguised.webm", sizes["disguised.webm"], "qa-mkv"))
    jobs = [accepted.json()["jobId"], mismatch.json()["jobId"], disguised.json()["jobId"]]
    snapshots = wait_terminal(client, jobs)
    summary["jobs"] = sanitize(snapshots)  # recorded before assertions so failures are diagnosable
    valid, size, fake = (snapshots[job] for job in jobs)
    check(valid["status"] == "completed" and valid["result"]["status"] == "unavailable", "valid -> unavailable")
    check(valid["result"]["result"]["error"] is None, "valid file has no file error")
    check(valid["result"]["result"]["analysis"]["error"]["code"] == "ANALYZER_NOT_CONFIGURED", "unconfigured")
    check(abs(valid["result"]["result"]["durationMs"] - 2000) <= 50, "decoded duration")
    check(size["result"]["result"]["error"]["code"] == "SOURCE_SIZE_MISMATCH", "size mismatch error")
    check(fake["result"]["result"]["error"]["code"] == "VIDEO_FORMAT_UNSUPPORTED", "MKV disguised as webm")
    entries = receiver_entries(work)
    check(len(entries) == 3 and all(entry["tokenOk"] for entry in entries), "one authenticated callback per job")
    for entry in entries:
        check(entry["body"] == snapshots[entry["body"]["jobId"]]["result"], "callback body equals GET.result")
    check(client.get("/analysis/video/jobs/unknown").status_code == 404, "unknown job 404")
    summary["callbacks"] = len(entries)


def scenario_callback_failure(client: httpx.Client, work: Path, sizes: dict[str, int], summary: dict[str, Any]) -> None:
    accepted = client.post("/analysis/video", json=body("valid.webm", sizes["valid.webm"], "qa-cb"))
    snapshot = wait_terminal(client, [accepted.json()["jobId"]])[accepted.json()["jobId"]]
    entries = receiver_entries(work)
    check(snapshot["callbackStatus"] == "failed", "400 from receiver ends delivery")
    check(len(entries) == 1, "permanent failure is not retried")
    check(snapshot["result"] == entries[0]["body"], "GET recovers the undelivered body")
    check(snapshot["status"] == "completed", "delivery failure never changes the analysis result")
    summary["jobs"] = sanitize({accepted.json()["jobId"]: snapshot})
    summary["callbacks"] = len(entries)


def scenario_expired_job(client: httpx.Client, work: Path, sizes: dict[str, int], summary: dict[str, Any]) -> None:
    request = body("valid.webm", sizes["valid.webm"], "qa-exp")
    job_id = client.post("/analysis/video", json=request).json()["jobId"]
    wait_terminal(client, [job_id])
    clock_file = work / "clock-offset"
    clock_file.write_text(str(90 * DAY + 60))
    check(client.get(f"/analysis/video/jobs/{job_id}").status_code == 410, "result expiry 410")
    check(client.post("/analysis/video", json=request).status_code == 410, "same request 410")
    changed = {**request, "userId": "8"}
    check(client.post("/analysis/video", json=changed).status_code == 409, "changed request 409")
    cleanup = build_cleanup(
        callback_settings=CallbackSecuritySettings(),
        video_settings=VideoSettings(),
        clock=OffsetClock(clock_file),
    )
    stats = cleanup.run_once()
    check(client.get(f"/analysis/video/jobs/{job_id}").status_code == 410, "still 410 after sweeping")
    clock_file.write_text(str(180 * DAY + 120))
    cleanup.run_once()
    check(client.get(f"/analysis/video/jobs/{job_id}").status_code == 404, "record expiry 404")
    fresh = client.post("/analysis/video", json=request)
    check(fresh.status_code == 202 and fresh.json()["jobId"] != job_id, "key reusable after 180 days")
    summary["cleanup"] = stats.__dict__
    summary["jobs"] = {"expired": job_id, "new": fresh.json()["jobId"]}


SCENARIOS = {
    "default": ("ok", scenario_default),
    "callback-failure": ("fail400", scenario_callback_failure),
    "expired-job": ("ok", scenario_expired_job),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="default")
    parser.add_argument("--require-real-media", action="store_true")
    args = parser.parse_args()
    if not TOOLS_AVAILABLE:
        print("BLOCKED: ffmpeg/ffprobe are not installed")  # noqa: T201
        return 2 if args.require_real_media else 0
    mode, run = SCENARIOS[args.scenario]
    summary: dict[str, Any] = {"scenario": args.scenario, "substituted": ["external S3", "API-server receiver"]}
    with tempfile.TemporaryDirectory() as raw:
        work = Path(raw)
        sizes = prepare_media(work)
        env = environment(work, mode)
        for key in (
            "APP_ENVIRONMENT",
            "APP_INTERNAL_CALLBACK_TOKEN",
            "APP_CALLBACK_ALLOWED_HOSTS",
            "VIDEO_ENABLED",
            "VIDEO_API_TOKEN",
            "VIDEO_DATABASE_PATH",
            "VIDEO_ARTIFACT_ROOT",
            "VIDEO_POLICY__SOURCE_HOSTS",
        ):
            os.environ[key] = env[key]  # the in-process cleanup uses the same settings as the children
        port = free_port()
        try:
            with (
                processes(work, env, port),
                httpx.Client(
                    base_url=f"http://127.0.0.1:{port}/", headers={"X-Internal-Token": VIDEO_TOKEN}, timeout=30
                ) as client,
            ):
                wait_healthy(client)
                run(client, work, sizes, summary)
            summary["result"] = "PASS"
        except QaFailureError as exc:
            summary["result"] = f"FAIL: {exc}"
        summary["logs_contain_secrets"] = any(
            secret in (work / name).read_text()
            for name in ("api.log", "worker.log")
            for secret in (CALLBACK_TOKEN, VIDEO_TOKEN, "X-Amz-Signature=s1")
        )
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    (args.evidence_dir / "qa-summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps({"scenario": args.scenario, "result": summary["result"]}))  # noqa: T201
    return 0 if summary["result"] == "PASS" and not summary["logs_contain_secrets"] else 1


if __name__ == "__main__":
    sys.exit(main())
