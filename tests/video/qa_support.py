"""Test-only processes for the real-HTTP QA runner: the real app, a worker and a controlled clock.

Only two things are substituted: the external S3 download (fixture files) and the callback receiver.
Everything else — make_app, uvicorn, SQLite, the worker, FFprobe and the unconfigured analyzer — is real.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
from fastapi import FastAPI

from app.main.run import make_app
from app.main.video_worker import build_worker


class OffsetClock:
    """Wall clock plus an offset read from a file, so separate processes can be moved 90 days ahead."""

    def __init__(self, offset_file: Path) -> None:
        self.offset_file = offset_file

    def now(self) -> float:
        try:
            offset = float(self.offset_file.read_text() or 0)
        except FileNotFoundError:
            offset = 0.0
        return time.time() + offset


def clock_from_env() -> OffsetClock:
    return OffsetClock(Path(os.environ["QA_CLOCK_FILE"]))


def create_app() -> FastAPI:
    """``uvicorn tests.video.qa_support:create_app --factory`` — the real application, real settings."""
    return make_app(video_clock=clock_from_env())


def source_transport(fixtures: Path) -> httpx.MockTransport:
    async def chunks(path: Path) -> AsyncIterator[bytes]:
        data = path.read_bytes()
        for offset in range(0, len(data), 65536):
            yield data[offset : offset + 65536]

    def handler(request: httpx.Request) -> httpx.Response:
        path = fixtures / Path(request.url.path).name
        if not path.is_file():
            return httpx.Response(404)
        # A streamed body, like a real S3 response (a bytes body would be pre-read by httpx).
        return httpx.Response(200, content=chunks(path))

    return httpx.MockTransport(handler)


def receiver_transport(log: Path, token: str, mode: str) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        token_ok = request.headers.get("x-internal-token") == token
        status = 401 if not token_ok else (400 if mode == "fail400" else 204)
        with log.open("a") as stream:
            stream.write(
                json.dumps({"tokenOk": token_ok, "status": status, "body": json.loads(request.content)}) + "\n"
            )
        return httpx.Response(status)

    return httpx.MockTransport(handler)


async def run_lane(lane: str) -> None:
    worker = build_worker(
        (lane,),
        source_transport=source_transport(Path(os.environ["QA_FIXTURES"])),
        callback_transport=receiver_transport(
            Path(os.environ["QA_RECEIVER_LOG"]),
            os.environ["APP_INTERNAL_CALLBACK_TOKEN"],
            os.environ.get("QA_RECEIVER_MODE", "ok"),
        ),
        clock=clock_from_env(),
    )
    while True:
        if not await worker.run_once():
            await asyncio.sleep(0.2)


async def run_worker() -> None:
    await asyncio.gather(*(run_lane(lane) for lane in ("io", "cpu", "callback")))


if __name__ == "__main__":
    asyncio.run(run_worker())
