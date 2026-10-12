from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from app.core.common.video.errors import VideoStageError
from app.core.common.video.policy import VideoLimits
from app.core.common.video.ports import StageOutput
from app.outbound.adapters.video.media_backend import LocalVideoMediaBackend
from tests.video.helpers import SOURCE_HOST, FakeClock, repository, security, submit, video_request

CONTENT = b"0123456789" * 1000
LIMITS = VideoLimits(source_hosts=(SOURCE_HOST,), min_free_disk_bytes=0)


def backend(handler: object, **security_overrides: object) -> LocalVideoMediaBackend:
    return LocalVideoMediaBackend(security(**security_overrides), transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


async def _chunks() -> AsyncIterator[bytes]:
    for block in (CONTENT[:3], CONTENT[3:5000], CONTENT[5000:]):
        yield block


async def _short() -> AsyncIterator[bytes]:
    yield CONTENT[:10]


def chunked(request: httpx.Request) -> httpx.Response:
    # No Content-Length; arbitrary chunking.
    return httpx.Response(200, content=_chunks())


@pytest.mark.asyncio
async def test_stream_size_hash_and_atomic_publication(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    request = video_request(video={"fileSize": len(CONTENT)})
    job_id = submit(repo, tmp_path, request)
    task = repo.claim_stage({"io": 1}, 60)
    assert task is not None
    reservation = repo.reserve_artifact(task, "source")
    assert reservation is not None
    data = await backend(chunked).download(request, LIMITS, reservation)
    assert data["sha256"] == hashlib.sha256(CONTENT).hexdigest()
    assert data["bytes"] == len(CONTENT)
    assert Path(reservation.temp_path).read_bytes() == CONTENT
    assert Path(reservation.temp_path).stat().st_mode & 0o777 == 0o600
    assert not Path(reservation.final_path).exists()  # not published before the lease-checked commit
    assert repo.finish_stage(task, StageOutput(data=data, staged_artifacts=(reservation,)))
    assert Path(reservation.final_path).read_bytes() == CONTENT
    assert not Path(reservation.temp_path).exists()
    assert json.loads(repo.task_rows(job_id)["source"]["result"])["path"] == reservation.final_path
    assert (tmp_path / "artifacts" / job_id).stat().st_mode & 0o777 == 0o700


async def _attempt(tmp_path: Path, handler: object, size: int = len(CONTENT), **overrides: object) -> VideoStageError:
    repo = repository(tmp_path)
    request = video_request(video={"fileSize": size})
    submit(repo, tmp_path, request)
    task = repo.claim_stage({"io": 1}, 60)
    assert task is not None
    reservation = repo.reserve_artifact(task, "source")
    assert reservation is not None
    with pytest.raises(VideoStageError) as caught:
        await backend(handler, **overrides).download(request, LIMITS, reservation)
    assert not Path(reservation.temp_path).exists()  # failed attempts leave no temporary file
    assert not Path(reservation.final_path).exists()
    return caught.value


def status(code: int, **headers: str) -> object:
    return lambda request: httpx.Response(code, headers=headers)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "size", "code", "retry"),
    [
        (status(403), len(CONTENT), "SOURCE_ACCESS_DENIED_OR_EXPIRED", False),
        (status(404), len(CONTENT), "SOURCE_NOT_FOUND", False),
        (status(429), len(CONTENT), "SOURCE_DOWNLOAD_FAILED", True),
        (status(503), len(CONTENT), "SOURCE_DOWNLOAD_FAILED", True),
        (status(302, Location="https://evil.example.com/x"), len(CONTENT), "SOURCE_DOWNLOAD_FAILED", False),
        (
            lambda request: httpx.Response(200, content=gzip.compress(CONTENT), headers={"Content-Encoding": "gzip"}),
            len(CONTENT),
            "SOURCE_DOWNLOAD_FAILED",
            False,
        ),
        (chunked, len(CONTENT) - 1, "SOURCE_SIZE_MISMATCH", False),  # over the declared size
        (chunked, len(CONTENT) + 1, "SOURCE_SIZE_MISMATCH", False),  # under the declared size
        # A Content-Length matching the request never establishes success; counted bytes do.
        (
            lambda request: httpx.Response(200, content=_short(), headers={"Content-Length": str(len(CONTENT))}),
            len(CONTENT),
            "SOURCE_SIZE_MISMATCH",
            False,
        ),
    ],
)
async def test_download_failure_matrix_and_cleanup(
    tmp_path: Path, handler: object, size: int, code: str, retry: bool
) -> None:
    error = await _attempt(tmp_path, handler, size)
    assert (error.code, error.stage_retry) == (code, retry)


@pytest.mark.asyncio
async def test_network_errors_blocks_and_cancellation(tmp_path: Path) -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    assert (await _attempt(tmp_path / "a", broken)).code == "SOURCE_DOWNLOAD_FAILED"
    calls: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=CONTENT)

    blocked = await _attempt(tmp_path / "b", recording, blocked_source_hosts=(SOURCE_HOST,))
    assert blocked.code == "SOURCE_ACCESS_DENIED_OR_EXPIRED"
    assert calls == []  # current emergency block: zero HTTP requests

    repo = repository(tmp_path / "c")
    request = video_request(video={"fileSize": len(CONTENT)})
    submit(repo, tmp_path / "c", request)
    task = repo.claim_stage({"io": 1}, 60)
    assert task is not None
    reservation = repo.reserve_artifact(task, "source")
    assert reservation is not None

    async def endless() -> AsyncIterator[bytes]:
        yield CONTENT[:10]
        await asyncio.sleep(30)
        yield CONTENT[10:]

    slow = backend(lambda request: httpx.Response(200, content=endless()))
    running = asyncio.create_task(slow.download(request, LIMITS, reservation))
    await asyncio.sleep(0.1)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert not Path(reservation.temp_path).exists()


@pytest.mark.asyncio
async def test_stale_publisher_cannot_replace_current_artifact(tmp_path: Path) -> None:
    clock = FakeClock()
    repo = repository(tmp_path, clock)
    job_id = submit(repo, tmp_path)
    stale = repo.claim_stage({"io": 1}, 60)
    assert stale is not None
    stale_reservation = repo.reserve_artifact(stale, "source")
    assert stale_reservation is not None
    Path(stale_reservation.temp_path).write_bytes(b"stale")
    clock.advance(63)
    current = repo.claim_stage({"io": 1}, 60)
    assert current is not None
    current_reservation = repo.reserve_artifact(current, "source")
    assert current_reservation is not None
    assert current_reservation.final_path != stale_reservation.final_path  # lease-unique paths
    Path(current_reservation.temp_path).write_bytes(b"current")
    assert repo.finish_stage(
        current, StageOutput(data={"path": current_reservation.final_path}, staged_artifacts=(current_reservation,))
    )
    assert not repo.finish_stage(
        stale, StageOutput(data={"path": stale_reservation.final_path}, staged_artifacts=(stale_reservation,))
    )
    assert Path(current_reservation.final_path).read_bytes() == b"current"
    assert not Path(stale_reservation.final_path).exists()
    assert json.loads(repo.task_rows(job_id)["source"]["result"])["path"] == current_reservation.final_path
