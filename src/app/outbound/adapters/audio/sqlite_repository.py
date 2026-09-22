from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.core.common.audio.dto import AudioPolicy, AudioRequest, IdempotencyConflictError, digest
from app.core.common.audio.preprocessing import DEPENDENCIES

TERMINAL = ("succeeded", "failed", "blocked")


class SqliteAudioRepository:
    """Single-host durable queue. All worker processes share this database and artifact disk."""

    def __init__(self, path: Path, max_attempts: int = 3) -> None:
        self.path = path
        self.max_attempts = max_attempts
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS audio_jobs (
                    id TEXT PRIMARY KEY, request_id TEXT NOT NULL, session_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, request TEXT NOT NULL, policy TEXT NOT NULL,
                    created REAL NOT NULL, UNIQUE(request_id, session_id)
                );
                CREATE TABLE IF NOT EXISTS audio_tasks (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES audio_jobs(id),
                    answer_index INTEGER NOT NULL, stage TEXT NOT NULL, lane TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                    token TEXT, lease_until REAL, available REAL NOT NULL DEFAULT 0,
                    result TEXT, error TEXT, UNIQUE(job_id, answer_index, stage)
                );
                CREATE TABLE IF NOT EXISTS audio_dependencies (
                    task_id TEXT NOT NULL REFERENCES audio_tasks(id),
                    dependency_id TEXT NOT NULL REFERENCES audio_tasks(id),
                    PRIMARY KEY(task_id, dependency_id)
                );
                CREATE INDEX IF NOT EXISTS audio_tasks_ready ON audio_tasks(status, available, lane);
                CREATE INDEX IF NOT EXISTS audio_tasks_job ON audio_tasks(job_id);
            """)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def submit(self, request: AudioRequest, policy: AudioPolicy) -> str:
        fingerprint = digest({"request": request.model_dump(), "policy": policy.model_dump()})
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT id, fingerprint FROM audio_jobs WHERE request_id=? AND session_id=?",
                (request.request_id, request.session_id),
            ).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise IdempotencyConflictError
                return str(previous["id"])
            job_id = uuid.uuid4().hex
            connection.execute(
                "INSERT INTO audio_jobs VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    request.request_id,
                    request.session_id,
                    fingerprint,
                    request.model_dump_json(),
                    policy.model_dump_json(),
                    time.time(),
                ),
            )
            tasks: dict[tuple[int, str], str] = {}
            for index in range(len(request.answers)):
                for stage in DEPENDENCIES:
                    tasks[index, stage] = self._insert_task(connection, job_id, index, stage)
                for stage, dependencies in DEPENDENCIES.items():
                    for dependency in dependencies:
                        connection.execute(
                            "INSERT INTO audio_dependencies VALUES (?, ?)",
                            (tasks[index, stage], tasks[index, dependency]),
                        )
            aggregate = self._insert_task(connection, job_id, -1, "aggregate")
            callback = self._insert_task(connection, job_id, -1, "callback")
            for (_index, stage), task_id in tasks.items():
                if stage in {"timing", "fluency"}:
                    connection.execute("INSERT INTO audio_dependencies VALUES (?, ?)", (aggregate, task_id))
            connection.execute("INSERT INTO audio_dependencies VALUES (?, ?)", (callback, aggregate))
            return job_id

    @staticmethod
    def _insert_task(connection: sqlite3.Connection, job_id: str, index: int, stage: str) -> str:
        task_id = uuid.uuid4().hex
        lane = {"transcribe": "inference", "fluency": "llm", "callback": "callback"}.get(stage, "cpu")
        connection.execute(
            "INSERT INTO audio_tasks(id, job_id, answer_index, stage, lane) VALUES (?, ?, ?, ?, ?)",
            (task_id, job_id, index, stage, lane),
        )
        return task_id

    def claim(self, capacities: dict[str, int], lease_seconds: int) -> dict[str, Any] | None:
        now = time.time()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """UPDATE audio_tasks SET status=CASE WHEN attempts >= ? THEN 'failed' ELSE 'pending' END,
                   token=NULL, lease_until=NULL, error='worker_lease_expired'
                   WHERE status='running' AND lease_until <= ?""",
                (self.max_attempts, now),
            )
            for lane, capacity in capacities.items():
                running = connection.execute(
                    "SELECT COUNT(*) FROM audio_tasks WHERE lane=? AND status='running'",
                    (lane,),
                ).fetchone()[0]
                if running >= capacity:
                    continue
                row = connection.execute(
                    """SELECT t.* FROM audio_tasks t JOIN audio_jobs j ON j.id=t.job_id
                       WHERE t.status='pending' AND t.available <= ? AND t.lane=?
                       AND NOT EXISTS (
                         SELECT 1 FROM audio_dependencies d JOIN audio_tasks p ON p.id=d.dependency_id
                         WHERE d.task_id=t.id AND p.status NOT IN ('succeeded','failed','blocked')
                       ) ORDER BY j.created, t.answer_index, t.rowid LIMIT 1""",
                    (now, lane),
                ).fetchone()
                if row is None:
                    continue
                token = uuid.uuid4().hex
                connection.execute(
                    """UPDATE audio_tasks SET status='running', attempts=attempts+1,
                       token=?, lease_until=? WHERE id=?""",
                    (token, now + lease_seconds, row["id"]),
                )
                task = dict(row)
                task.update(token=token, status="running", attempts=row["attempts"] + 1)
                return task
        return None

    def heartbeat(self, task_id: str, token: str, lease_seconds: int) -> bool:
        now = time.time()
        with self._connection() as connection:
            return (
                connection.execute(
                    """UPDATE audio_tasks SET lease_until=? WHERE id=? AND token=?
                   AND status='running' AND lease_until > ?""",
                    (now + lease_seconds, task_id, token, now),
                ).rowcount
                == 1
            )

    def context(self, task: dict[str, Any]) -> dict[str, Any]:
        with self._connection() as connection:
            job = connection.execute("SELECT * FROM audio_jobs WHERE id=?", (task["job_id"],)).fetchone()
            if task["stage"] == "aggregate":
                rows = connection.execute(
                    "SELECT * FROM audio_tasks WHERE job_id=? AND stage IN ('prepare','timing','fluency')",
                    (task["job_id"],),
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT p.* FROM audio_dependencies d JOIN audio_tasks p ON p.id=d.dependency_id
                       WHERE d.task_id=?""",
                    (task["id"],),
                ).fetchall()
        return {
            "request": json.loads(job["request"]),
            "policy": json.loads(job["policy"]),
            "tasks": [{**dict(row), "result": json.loads(row["result"]) if row["result"] else None} for row in rows],
        }

    def finish(self, task: dict[str, Any], result: dict[str, Any]) -> bool:
        status = "blocked" if result.get("stage_blocked") else "succeeded"
        with self._connection() as connection:
            return (
                connection.execute(
                    """UPDATE audio_tasks SET status=?, result=?, error=NULL, token=NULL, lease_until=NULL
                   WHERE id=? AND token=? AND status='running' AND lease_until > ?""",
                    (
                        status,
                        json.dumps(result, ensure_ascii=False, allow_nan=False),
                        task["id"],
                        task["token"],
                        time.time(),
                    ),
                ).rowcount
                == 1
            )

    def fail(self, task: dict[str, Any], code: str, *, retryable: bool) -> bool:
        retry = retryable and task["attempts"] < self.max_attempts
        with self._connection() as connection:
            return (
                connection.execute(
                    """UPDATE audio_tasks SET status=?, error=?, available=?, token=NULL, lease_until=NULL
                   WHERE id=? AND token=? AND status='running' AND lease_until > ?""",
                    (
                        "pending" if retry else "failed",
                        code,
                        time.time() + min(60, 2 ** task["attempts"]),
                        task["id"],
                        task["token"],
                        time.time(),
                    ),
                ).rowcount
                == 1
            )

    def snapshot(self, job_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            job = connection.execute("SELECT session_id FROM audio_jobs WHERE id=?", (job_id,)).fetchone()
            if job is None:
                return None
            tasks = connection.execute(
                "SELECT answer_index, stage, status, attempts, error FROM audio_tasks WHERE job_id=?",
                (job_id,),
            ).fetchall()
            stored_result = connection.execute(
                "SELECT result FROM audio_tasks WHERE job_id=? AND stage='aggregate'",
                (job_id,),
            ).fetchone()["result"]
        aggregate = next(row for row in tasks if row["stage"] == "aggregate")
        callback = next(row for row in tasks if row["stage"] == "callback")
        return {
            "jobId": job_id,
            "sessionId": job["session_id"],
            "status": "completed"
            if aggregate["status"] == "succeeded"
            else ("failed" if aggregate["status"] in TERMINAL else "processing"),
            "completedTasks": sum(row["status"] in TERMINAL for row in tasks),
            "totalTasks": len(tasks),
            "callbackStatus": callback["status"],
            "result": json.loads(stored_result) if stored_result else None,
            "tasks": [
                {key: row[key] for key in ("answer_index", "stage", "status", "attempts", "error")} for row in tasks
            ],
        }
