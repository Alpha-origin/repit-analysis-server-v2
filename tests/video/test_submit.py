from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from app.core.common.video.identity import request_fingerprint
from app.core.common.video.policy import VideoLimits
from app.core.common.video.ports import AdmissionError, ReplayConflictError
from app.outbound.adapters.video.sqlite_repository import SqliteVideoRepository
from tests.video.helpers import (
    CALLBACK_HOST,
    SOURCE_HOST,
    FakeClock,
    admit_for,
    file_url,
    repository,
    security,
    video_request,
)


def _submit(repo: SqliteVideoRepository, tmp_path: Path, request: Any = None, **admit: Any) -> str:
    request = request or video_request()
    return repo.submit(request, request_fingerprint(request), admit_for(tmp_path, **admit)).job_id


def _count(repo: SqliteVideoRepository, table: str) -> int:
    with sqlite3.connect(repo.path) as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608


def test_replay_after_policy_change_preserves_job(tmp_path: Path) -> None:
    clock = FakeClock()
    repo = repository(tmp_path, clock)
    job_id = _submit(repo, tmp_path)
    original = repo.job_row(job_id)
    assert original is not None
    clock.advance(60)
    # The operator tightens every limit and empties the host lists: replays still recover the same job.
    tightened = {
        "policy": VideoLimits(source_hosts=(), max_bytes=1, max_job_disk_bytes=1, min_free_disk_bytes=0),
        "stage_max_attempts": 1,
        "stage_retry_delays_seconds": (),
    }
    resigned = video_request(video={"fileUrl": file_url("re-signed")})
    replay = repo.submit(
        resigned,
        request_fingerprint(resigned),
        admit_for(tmp_path, provider=security(allowed_callback_hosts=()), **tightened),
    )
    assert replay.job_id == job_id
    assert replay.created is False
    after = repo.job_row(job_id)
    assert after == original  # creation time, policy and the ORIGINAL raw URL are untouched
    assert json.loads(after["request"])["video"]["fileUrl"] == file_url()
    policy = json.loads(after["policy"])
    assert policy["limits"]["max_bytes"] == 1_000_000_000
    assert policy["admitted_callback_hosts"] == [CALLBACK_HOST]
    assert policy["stage_max_attempts"] == 3


def test_conflict_and_transaction_rollback(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    _submit(repo, tmp_path)
    for changed in (
        video_request(callbackUrl=f"https://{CALLBACK_HOST}/other"),
        video_request(video={"fileUrl": file_url(extra="&versionId=2")}),
    ):
        with pytest.raises(ReplayConflictError):
            _submit(repo, tmp_path, changed)

    other = tmp_path / "other"
    other.mkdir()
    fresh = repository(other)

    def failing_dag(*args: Any) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    fresh._insert_dag = failing_dag  # type: ignore[method-assign, assignment]
    with pytest.raises(Exception):  # noqa: B017, PT011 — any storage error must abort the acceptance
        _submit(fresh, other)
    assert _count(fresh, "video_jobs") == 0
    assert _count(fresh, "video_tasks") == 0


def test_admission_errors_only_for_new_requests(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    too_big = video_request(requestId="big", video={"fileSize": 2_000_000_000})
    with pytest.raises(AdmissionError, match="video_too_large"):
        _submit(repo, tmp_path, too_big)
    foreign = video_request(requestId="foreign", video={"fileUrl": "https://other.s3.amazonaws.com/v.webm"})
    with pytest.raises(AdmissionError, match="source_url_not_allowed"):
        _submit(repo, tmp_path, foreign)
    blocked = video_request(requestId="blocked")
    with pytest.raises(AdmissionError, match="source_url_not_allowed"):
        _submit(repo, tmp_path, blocked, provider=security(blocked_source_hosts=(SOURCE_HOST,)))
    with pytest.raises(AdmissionError, match="callback_url_not_allowed"):
        _submit(repo, tmp_path, video_request(requestId="cb"), provider=security(allowed_callback_hosts=()))
    assert _count(repo, "video_jobs") == 0
