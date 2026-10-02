from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.outbound.adapters.video.sqlite_repository import SCHEMA_VERSION, UnsupportedSchemaVersionError
from tests.video.helpers import repository, submit

TABLES = {
    "video_jobs",
    "video_tasks",
    "video_dependencies",
    "video_results",
    "video_callbacks",
    "video_artifacts",
    "video_tombstones",
}


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def test_create_and_reopen_schema(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    job_id = submit(repo, tmp_path)
    with _connect(repo.path) as connection:
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert names == TABLES  # isolated from audio_* tables
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    reopened = repository(tmp_path)  # no-op migration keeps data
    assert reopened.job_row(job_id) is not None
    assert len(reopened.task_rows(job_id)) == 5


def test_constraints_and_future_version_rejection(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    job_id = submit(repo, tmp_path)
    with _connect(repo.path) as connection:
        with pytest.raises(sqlite3.IntegrityError):  # duplicate (sessionId, requestId)
            connection.execute(
                "INSERT INTO video_jobs (id, session_id, request_id, identity_version, fingerprint, request, policy,"
                " status, created_at) SELECT 'x', session_id, request_id, identity_version, fingerprint, request,"
                " policy, 'processing', 0 FROM video_jobs"
            )
        with pytest.raises(sqlite3.IntegrityError):  # orphan task
            connection.execute(
                "INSERT INTO video_tasks (id, job_id, stage, lane, max_attempts)"
                " VALUES ('t', 'nope', 'source', 'io', 1)"
            )
        with pytest.raises(sqlite3.IntegrityError):  # second stage row
            connection.execute(
                "INSERT INTO video_tasks (id, job_id, stage, lane, max_attempts) VALUES ('t2', ?, 'source', 'io', 1)",
                (job_id,),
            )
        with pytest.raises(sqlite3.IntegrityError):  # orphan dependency
            connection.execute("INSERT INTO video_dependencies VALUES ('missing', 'missing2')")
        with pytest.raises(sqlite3.IntegrityError):  # outbox for unknown job
            connection.execute(
                "INSERT INTO video_callbacks (job_id, status, max_attempts, next_at, updated_at)"
                " VALUES ('nope', 'pending', 6, 0, 0)"
            )
        with pytest.raises(sqlite3.IntegrityError):  # processing job cannot carry a finish time
            connection.execute("UPDATE video_jobs SET finished_at=1 WHERE id=?", (job_id,))
        # GC manifests deliberately survive without a job row.
        connection.execute(
            "INSERT INTO video_artifacts (id, job_id, stage, lease_token, temp_path, final_path, state, created_at)"
            " VALUES ('a', 'expired-job', 'source', 't', '/x.partial', '/x', 'published', 0)"
        )
        connection.execute("PRAGMA user_version=99")
    with pytest.raises(UnsupportedSchemaVersionError):
        repository(tmp_path)
