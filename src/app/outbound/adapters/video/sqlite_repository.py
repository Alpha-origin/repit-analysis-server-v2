"""Single-host durable video queue, terminal store, callback outbox and retention records.

Every state change is one SQLite transaction. Writes from a worker are fenced by the lease token it
received at claim time, so a stale worker can never overwrite a newer owner's result or files.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from app.core.common.video.dto import JobSnapshot, TerminalCallback, VideoRequest
from app.core.common.video.errors import PublicErrorCode
from app.core.common.video.identity import IDENTITY_VERSION, UnsupportedIdentityVersionError, request_fingerprint
from app.core.common.video.policy import GIB, AcceptedVideoPolicy
from app.core.common.video.ports import (
    DEPENDENCIES,
    LANES,
    STAGES,
    Accepted,
    AdmissionError,
    Admit,
    ArtifactReservation,
    CallbackClaim,
    CleanupStats,
    Clock,
    DeliveryDecision,
    GcCandidate,
    ReplayConflictError,
    ReplayGoneError,
    RepositoryUnavailableError,
    SnapshotFound,
    SnapshotGone,
    SnapshotMissing,
    SnapshotOutcome,
    Stage,
    StageContext,
    StageOutput,
    StageRecord,
    StageTask,
    VideoResourceUnavailableError,
)
from app.core.common.video.terminal import failed_callback

SCHEMA_VERSION = 2
DAY = 86_400
HOUR = 3_600
TERMINAL_TASK = ("succeeded", "failed", "blocked")

_MAINTENANCE_SCHEMA = """
CREATE TABLE video_maintenance (
    id INTEGER PRIMARY KEY CHECK (id=1),
    last_pass_at REAL NOT NULL,
    last_success_at REAL NOT NULL,
    artifact_errors INTEGER NOT NULL
)
"""

_SCHEMA = (
    """
CREATE TABLE video_jobs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    identity_version TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    request TEXT NOT NULL,
    policy TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('processing', 'completed', 'failed')),
    created_at REAL NOT NULL,
    finished_at REAL,
    result_expires_at REAL,
    tombstone_expires_at REAL,
    artifact_gc_after REAL,
    UNIQUE (session_id, request_id),
    CHECK ((status = 'processing') = (finished_at IS NULL)),
    CHECK ((finished_at IS NULL) = (result_expires_at IS NULL)),
    CHECK ((finished_at IS NULL) = (tombstone_expires_at IS NULL))
);
CREATE TABLE video_tasks (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES video_jobs(id) ON DELETE CASCADE,
    stage TEXT NOT NULL CHECK (stage IN ('source', 'inspect', 'validate', 'analyze', 'finalize')),
    lane TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'blocked')),
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL CHECK (max_attempts >= 1),
    token TEXT,
    lease_until REAL,
    available_at REAL NOT NULL DEFAULT 0,
    result TEXT,
    error_code TEXT,
    UNIQUE (job_id, stage)
);
CREATE TABLE video_dependencies (
    task_id TEXT NOT NULL REFERENCES video_tasks(id) ON DELETE CASCADE,
    dependency_id TEXT NOT NULL REFERENCES video_tasks(id) ON DELETE CASCADE,
    PRIMARY KEY (task_id, dependency_id)
);
CREATE TABLE video_results (
    job_id TEXT PRIMARY KEY REFERENCES video_jobs(id) ON DELETE CASCADE,
    body TEXT NOT NULL
);
CREATE TABLE video_callbacks (
    job_id TEXT PRIMARY KEY REFERENCES video_jobs(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('pending', 'sending', 'delivered', 'failed')),
    reservations INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL,
    next_at REAL NOT NULL,
    token TEXT,
    lease_until REAL,
    last_outcome TEXT,
    updated_at REAL NOT NULL,
    CHECK (reservations <= max_attempts)
);
-- No foreign key on purpose: GC manifests must outlive expired jobs until the files are gone.
CREATE TABLE video_artifacts (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    lease_token TEXT NOT NULL,
    temp_path TEXT NOT NULL UNIQUE,
    final_path TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK (state IN ('staged', 'published', 'collected')),
    created_at REAL NOT NULL,
    collected_at REAL,
    reserved_bytes INTEGER NOT NULL DEFAULT 0 CHECK (reserved_bytes >= 0)
);
CREATE TABLE video_tombstones (
    session_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    job_id TEXT NOT NULL UNIQUE,
    identity_version TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    expires_at REAL NOT NULL,
    PRIMARY KEY (session_id, request_id)
);
CREATE INDEX video_tasks_ready ON video_tasks (status, lane, available_at);
CREATE INDEX video_tasks_job ON video_tasks (job_id);
CREATE INDEX video_callbacks_due ON video_callbacks (status, next_at);
CREATE INDEX video_jobs_expiry ON video_jobs (status, result_expires_at);
CREATE INDEX video_artifacts_gc ON video_artifacts (state, job_id);
CREATE INDEX video_tombstones_expiry ON video_tombstones (expires_at);
"""
    + _MAINTENANCE_SCHEMA
)


class UnsupportedSchemaVersionError(RuntimeError):
    pass


def iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


class SqliteVideoRepository:
    def __init__(self, path: Path, artifact_root: Path, clock: Clock, *, disk_budget_bytes: int = 20 * GIB) -> None:
        if disk_budget_bytes <= 0:
            raise ValueError("disk budget must be positive")
        self.disk_budget_bytes = disk_budget_bytes
        self.path = path
        self.clock = clock
        self.artifact_root = artifact_root.resolve()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.artifact_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._write() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise UnsupportedSchemaVersionError(f"video schema {version} is newer than {SCHEMA_VERSION}")
            if version == 0:
                for statement in _SCHEMA.split(";"):
                    if statement.strip():
                        connection.execute(statement)
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            elif version == 1:
                connection.execute(_MAINTENANCE_SCHEMA)
                connection.execute(
                    "ALTER TABLE video_artifacts ADD COLUMN reserved_bytes INTEGER NOT NULL DEFAULT 0 "
                    "CHECK (reserved_bytes >= 0)"
                )
                # Reserve conservatively for historical manifests, including orphaned files.
                rows = connection.execute(
                    "SELECT id, job_id, temp_path, final_path FROM video_artifacts WHERE state != 'collected'"
                ).fetchall()
                for row in rows:
                    job = connection.execute("SELECT request FROM video_jobs WHERE id=?", (row["job_id"],)).fetchone()
                    expected = json.loads(job["request"])["video"]["fileSize"] if job else 0
                    actual = sum(
                        Path(row[key]).lstat().st_size for key in ("temp_path", "final_path") if Path(row[key]).exists()
                    )
                    connection.execute(
                        "UPDATE video_artifacts SET reserved_bytes=? WHERE id=?", (max(expected, actual), row["id"])
                    )
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    # ------------------------------------------------------------------ transactions

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        try:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    yield connection
                except BaseException:
                    connection.execute("ROLLBACK")
                    raise
                connection.execute("COMMIT")
            finally:
                connection.close()
        except sqlite3.OperationalError as exc:
            raise RepositoryUnavailableError from exc

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        """One consistent WAL snapshot for every SELECT inside the block."""
        try:
            connection = self._connect()
            try:
                connection.execute("BEGIN")
                try:
                    yield connection
                finally:
                    connection.execute("ROLLBACK")
            finally:
                connection.close()
        except sqlite3.OperationalError as exc:
            raise RepositoryUnavailableError from exc

    # ------------------------------------------------------------------ acceptance

    def submit(self, request: VideoRequest, fingerprint: str, admit: Admit) -> Accepted:
        with self._write() as connection:
            # Replay/admission outcomes are returned, not raised, so an expiry conversion done in this
            # transaction still commits before the caller sees 409/410/422.
            outcome = self._submit(connection, request, fingerprint, admit)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    @staticmethod
    def _versioned_fingerprint(request: VideoRequest, version: str) -> str:
        try:
            return request_fingerprint(request, version)
        except UnsupportedIdentityVersionError as exc:
            raise RepositoryUnavailableError from exc

    def _submit(
        self, connection: sqlite3.Connection, request: VideoRequest, fingerprint: str, admit: Admit
    ) -> Accepted | Exception:
        now = self.clock.now()
        job = connection.execute(
            "SELECT id, identity_version, fingerprint, status, result_expires_at FROM video_jobs "
            "WHERE session_id=? AND request_id=?",
            (request.session_id, request.request_id),
        ).fetchone()
        if job is not None and job["status"] != "processing" and now >= job["result_expires_at"]:
            # Logical expiry must not depend on the sweeper having run.
            self._expire_job(connection, job["id"])
            job = None
        if job is not None:
            if job["fingerprint"] != self._versioned_fingerprint(request, job["identity_version"]):
                return ReplayConflictError()
            return Accepted(job_id=job["id"], created=False)
        tombstone = connection.execute(
            "SELECT identity_version, fingerprint, expires_at FROM video_tombstones "
            "WHERE session_id=? AND request_id=?",
            (request.session_id, request.request_id),
        ).fetchone()
        if tombstone is not None:
            if now < tombstone["expires_at"]:
                same = tombstone["fingerprint"] == self._versioned_fingerprint(request, tombstone["identity_version"])
                return ReplayGoneError() if same else ReplayConflictError()
            connection.execute(
                "DELETE FROM video_tombstones WHERE session_id=? AND request_id=?",
                (request.session_id, request.request_id),
            )
        # Only genuinely new requests meet the current admission policy.
        try:
            policy = admit(request)
        except AdmissionError as exc:
            return exc
        job_id = uuid.uuid4().hex
        connection.execute(
            """INSERT INTO video_jobs (id, session_id, request_id, identity_version, fingerprint, request,
               policy, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'processing', ?)""",
            (
                job_id,
                request.session_id,
                request.request_id,
                policy.identity_version,
                self._versioned_fingerprint(request, policy.identity_version),
                _dumps(request.wire()),
                _dumps(policy.model_dump(mode="json")),
                now,
            ),
        )
        self._insert_dag(connection, job_id, policy)
        return Accepted(job_id=job_id, created=True)

    @staticmethod
    def _insert_dag(connection: sqlite3.Connection, job_id: str, policy: AcceptedVideoPolicy) -> None:
        ids: dict[Stage, str] = {}
        for stage in STAGES:
            ids[stage] = uuid.uuid4().hex
            connection.execute(
                "INSERT INTO video_tasks (id, job_id, stage, lane, max_attempts) VALUES (?, ?, ?, ?, ?)",
                (ids[stage], job_id, stage, LANES[stage], policy.stage_max_attempts),
            )
        for stage, dependencies in DEPENDENCIES.items():
            for dependency in dependencies:
                connection.execute(
                    "INSERT INTO video_dependencies (task_id, dependency_id) VALUES (?, ?)",
                    (ids[stage], ids[dependency]),
                )

    # ------------------------------------------------------------------ lookup

    def snapshot(self, job_id: str) -> SnapshotOutcome:
        with self._read() as connection:
            now = self.clock.now()
            job = connection.execute("SELECT * FROM video_jobs WHERE id=?", (job_id,)).fetchone()
            if job is not None:
                if job["status"] != "processing":
                    if now >= job["tombstone_expires_at"]:
                        return SnapshotMissing()
                    if now >= job["result_expires_at"]:
                        return SnapshotGone()
                return SnapshotFound(self._job_snapshot(connection, job))
            tombstone = connection.execute(
                "SELECT expires_at FROM video_tombstones WHERE job_id=?", (job_id,)
            ).fetchone()
            if tombstone is not None and now < tombstone["expires_at"]:
                return SnapshotGone()
            return SnapshotMissing()

    @staticmethod
    def _job_snapshot(connection: sqlite3.Connection, job: sqlite3.Row) -> JobSnapshot:
        body = connection.execute("SELECT body FROM video_results WHERE job_id=?", (job["id"],)).fetchone()
        callback = connection.execute("SELECT status FROM video_callbacks WHERE job_id=?", (job["id"],)).fetchone()
        result = TerminalCallback.model_validate(json.loads(body["body"])) if body is not None else None
        return JobSnapshot(
            job_id=job["id"],
            request_id=job["request_id"],
            session_id=job["session_id"],
            status=job["status"],
            created_at=iso(job["created_at"]),
            finished_at=iso(job["finished_at"]) if job["finished_at"] is not None else None,
            callback_status=callback["status"] if callback is not None else "pending",
            result=result,
            error=result.error if result is not None and job["status"] == "failed" else None,
        )

    # ------------------------------------------------------------------ stage execution

    def claim_stage(self, capacities: dict[str, int], lease_seconds: int) -> StageTask | None:
        with self._write() as connection:
            now = self.clock.now()
            self._reclaim_expired_stage_leases(connection, now)
            for lane, capacity in capacities.items():
                running = connection.execute(
                    "SELECT COUNT(*) FROM video_tasks WHERE lane=? AND status='running'", (lane,)
                ).fetchone()[0]
                if running >= capacity:
                    continue
                row = connection.execute(
                    """SELECT t.id, t.job_id, t.stage, t.attempts FROM video_tasks t
                       JOIN video_jobs j ON j.id = t.job_id
                       WHERE t.status='pending' AND t.lane=? AND t.available_at <= ? AND j.status='processing'
                       AND NOT EXISTS (
                         SELECT 1 FROM video_dependencies d JOIN video_tasks p ON p.id = d.dependency_id
                         WHERE d.task_id = t.id AND p.status NOT IN ('succeeded', 'failed', 'blocked')
                       )
                       ORDER BY j.created_at, t.rowid LIMIT 1""",
                    (lane, now),
                ).fetchone()
                if row is None:
                    continue
                token = uuid.uuid4().hex
                connection.execute(
                    """UPDATE video_tasks SET status='running', attempts=attempts+1, token=?, lease_until=?
                       WHERE id=?""",
                    (token, now + lease_seconds, row["id"]),
                )
                return StageTask(
                    id=row["id"],
                    job_id=row["job_id"],
                    stage=cast("Stage", row["stage"]),
                    token=token,
                    attempts=row["attempts"] + 1,
                )
        return None

    def _reclaim_expired_stage_leases(self, connection: sqlite3.Connection, now: float) -> None:
        expired = connection.execute(
            """SELECT id, job_id, stage, attempts, max_attempts, lease_until FROM video_tasks
               WHERE status='running' AND lease_until <= ?""",
            (now,),
        ).fetchall()
        for row in expired:
            if row["attempts"] >= row["max_attempts"]:
                self._fail_final(connection, row["id"], row["job_id"], row["stage"], "PROCESSING_TIMEOUT", now)
            else:
                delay = self._stage_delay(connection, row["job_id"], row["attempts"])
                connection.execute(
                    """UPDATE video_tasks SET status='pending', token=NULL, lease_until=NULL,
                       error_code='PROCESSING_TIMEOUT', available_at=? WHERE id=?""",
                    (row["lease_until"] + delay, row["id"]),
                )

    @staticmethod
    def _policy(connection: sqlite3.Connection, job_id: str) -> AcceptedVideoPolicy:
        row = connection.execute("SELECT policy FROM video_jobs WHERE id=?", (job_id,)).fetchone()
        return AcceptedVideoPolicy.model_validate(json.loads(row["policy"]))

    def _stage_delay(self, connection: sqlite3.Connection, job_id: str, attempts: int) -> int:
        delays = self._policy(connection, job_id).stage_retry_delays_seconds
        return delays[min(attempts, len(delays)) - 1] if delays else 0

    @staticmethod
    def _owned(connection: sqlite3.Connection, task: StageTask, now: float) -> bool:
        return (
            connection.execute(
                """SELECT 1 FROM video_tasks WHERE id=? AND token=? AND status='running' AND lease_until > ?""",
                (task.id, task.token, now),
            ).fetchone()
            is not None
        )

    def heartbeat(self, task: StageTask, lease_seconds: int) -> bool:
        with self._write() as connection:
            now = self.clock.now()
            return (
                connection.execute(
                    """UPDATE video_tasks SET lease_until=? WHERE id=? AND token=? AND status='running'
                       AND lease_until > ?""",
                    (now + lease_seconds, task.id, task.token, now),
                ).rowcount
                == 1
            )

    def stage_context(self, task: StageTask) -> StageContext:
        with self._read() as connection:
            job = connection.execute("SELECT request, policy FROM video_jobs WHERE id=?", (task.job_id,)).fetchone()
            # Every other stage of the job, not only direct dependencies: validate needs the source path.
            rows = connection.execute(
                "SELECT stage, status, result, error_code FROM video_tasks WHERE job_id=? AND id != ?",
                (task.job_id, task.id),
            ).fetchall()
        return StageContext(
            request=VideoRequest.model_validate(json.loads(job["request"])),
            policy=AcceptedVideoPolicy.model_validate(json.loads(job["policy"])),
            dependencies={
                cast("Stage", row["stage"]): StageRecord(
                    stage=cast("Stage", row["stage"]),
                    status=row["status"],
                    result=json.loads(row["result"]) if row["result"] else None,
                    error_code=cast("PublicErrorCode | None", row["error_code"]),
                )
                for row in rows
            },
        )

    def reserve_artifact(self, task: StageTask, suffix: str) -> ArtifactReservation | None:
        """Record temp and final paths *before* any byte is written, so every file stays discoverable."""
        directory = self.artifact_root / task.job_id / task.stage
        reservation = ArtifactReservation(
            id=uuid.uuid4().hex,
            temp_path=str(directory / f"{task.token}.{suffix}.partial"),
            final_path=str(directory / f"{task.token}.{suffix}"),
        )
        with self._write() as connection:
            now = self.clock.now()
            if not self._owned(connection, task, now):
                return None
            request_row = connection.execute("SELECT request FROM video_jobs WHERE id=?", (task.job_id,)).fetchone()
            expected = VideoRequest.model_validate_json(request_row["request"]).video.file_size
            policy = self._policy(connection, task.job_id)
            self._reserve_disk(connection, expected, policy.limits.min_free_disk_bytes)
            connection.execute(
                """INSERT INTO video_artifacts (id, job_id, stage, lease_token, temp_path, final_path, state,
                   created_at, reserved_bytes) VALUES (?, ?, ?, ?, ?, ?, 'staged', ?, ?)""",
                (
                    reservation.id,
                    task.job_id,
                    task.stage,
                    task.token,
                    reservation.temp_path,
                    reservation.final_path,
                    now,
                    expected,
                ),
            )
        self._private_directory(directory)
        return reservation

    def _reserve_disk(self, connection: sqlite3.Connection, expected: int, minimum_free: int) -> None:
        total, staged = connection.execute(
            "SELECT COALESCE(SUM(reserved_bytes),0), "
            "COALESCE(SUM(CASE WHEN state='staged' THEN reserved_bytes ELSE 0 END),0) "
            "FROM video_artifacts WHERE state != 'collected'"
        ).fetchone()
        # Staged reservations are counted in full even after partial writes: intentionally conservative.
        free = shutil.disk_usage(self.artifact_root).free
        if total + expected > self.disk_budget_bytes or free - staged - expected < minimum_free:
            raise VideoResourceUnavailableError

    def defer_stage(self, task: StageTask, delay_seconds: int = 5) -> bool:
        with self._write() as connection:
            now = self.clock.now()
            if not self._owned(connection, task, now):
                return False
            job = connection.execute("SELECT created_at FROM video_jobs WHERE id=?", (task.job_id,)).fetchone()
            timeout = self._policy(connection, task.job_id).limits.resource_wait_timeout_seconds
            if now >= job["created_at"] + timeout:
                self._fail_final(connection, task.id, task.job_id, task.stage, "PROCESSING_TIMEOUT", now)
                return True
            return (
                connection.execute(
                    "UPDATE video_tasks SET status='pending', attempts=attempts-1, token=NULL, lease_until=NULL, "
                    "available_at=? WHERE id=?",
                    (now + delay_seconds, task.id),
                ).rowcount
                == 1
            )

    def release_empty_artifacts(self, task: StageTask) -> None:
        # Called only after the execution coroutine (and any child) has stopped. Lease expiry alone
        # never releases capacity, since an old writer might still be alive.
        with self._write() as connection:
            rows = connection.execute(
                "SELECT id, temp_path, final_path FROM video_artifacts "
                "WHERE job_id=? AND lease_token=? AND state='staged'",
                (task.job_id, task.token),
            ).fetchall()
            for row in rows:
                if not any(
                    Path(row[key]).exists() or Path(row[key]).is_symlink() for key in ("temp_path", "final_path")
                ):
                    connection.execute(
                        "UPDATE video_artifacts SET state='collected', collected_at=? WHERE id=?",
                        (self.clock.now(), row["id"]),
                    )

    def _private_directory(self, directory: Path) -> None:
        current = self.artifact_root
        for part in directory.relative_to(self.artifact_root).parts:
            current = current / part
            if current.is_symlink():
                raise OSError("artifact directory must not be a symlink")
            current.mkdir(mode=0o700, exist_ok=True)

    def finish_stage(self, task: StageTask, output: StageOutput) -> bool:
        with self._write() as connection:
            now = self.clock.now()
            if not self._owned(connection, task, now):
                return False
            for artifact in output.staged_artifacts:
                row = connection.execute(
                    """SELECT temp_path, final_path FROM video_artifacts WHERE id=? AND job_id=? AND stage=?
                       AND lease_token=? AND state='staged'""",
                    (artifact.id, task.job_id, task.stage, task.token),
                ).fetchone()
                if row is None:
                    raise RuntimeError("artifact reservation is not owned by this lease")
                # rename is not transactional; on rollback the staged manifest still lists both paths.
                Path(row["temp_path"]).replace(row["final_path"])
                connection.execute("UPDATE video_artifacts SET state='published' WHERE id=?", (artifact.id,))
            connection.execute(
                """UPDATE video_tasks SET status='succeeded', result=?, error_code=NULL, token=NULL,
                   lease_until=NULL WHERE id=?""",
                (_dumps(output.data), task.id),
            )
            return True

    def block_stage(self, task: StageTask, cause: PublicErrorCode) -> bool:
        with self._write() as connection:
            now = self.clock.now()
            if not self._owned(connection, task, now):
                return False
            connection.execute(
                """UPDATE video_tasks SET status='blocked', result=NULL, error_code=?, token=NULL, lease_until=NULL
                   WHERE id=?""",
                (cause, task.id),
            )
            return True

    def fail_stage(self, task: StageTask, code: PublicErrorCode, *, stage_retry: bool) -> bool:
        with self._write() as connection:
            now = self.clock.now()
            row = connection.execute(
                """SELECT attempts, max_attempts FROM video_tasks WHERE id=? AND token=? AND status='running'
                   AND lease_until > ?""",
                (task.id, task.token, now),
            ).fetchone()
            if row is None:
                return False
            if stage_retry and row["attempts"] < row["max_attempts"]:
                connection.execute(
                    """UPDATE video_tasks SET status='pending', error_code=?, token=NULL, lease_until=NULL,
                       available_at=? WHERE id=?""",
                    (code, now + self._stage_delay(connection, task.job_id, row["attempts"]), task.id),
                )
            else:
                self._fail_final(connection, task.id, task.job_id, task.stage, code, now)
            return True

    def _fail_final(
        self, connection: sqlite3.Connection, task_id: str, job_id: str, stage: str, code: str, now: float
    ) -> None:
        connection.execute(
            "UPDATE video_tasks SET status='failed', error_code=?, token=NULL, lease_until=NULL WHERE id=?",
            (code, task_id),
        )
        if stage == "finalize":
            # The terminal document could not be produced: still converge with a minimal failed callback.
            request = VideoRequest.model_validate(
                json.loads(connection.execute("SELECT request FROM video_jobs WHERE id=?", (job_id,)).fetchone()[0])
            )
            self._terminalize(connection, job_id, failed_callback(job_id, request), now)

    def terminalize(self, task: StageTask, body: TerminalCallback) -> bool:
        if body.job_id != task.job_id:
            raise ValueError("terminal body belongs to another job")
        with self._write() as connection:
            now = self.clock.now()
            if not self._owned(connection, task, now):
                return False
            self._terminalize(connection, task.job_id, body, now)
            connection.execute(
                """UPDATE video_tasks SET status='succeeded', result=?, error_code=NULL, token=NULL,
                   lease_until=NULL WHERE id=?""",
                (_dumps({"status": body.status}), task.id),
            )
            return True

    def _terminalize(self, connection: sqlite3.Connection, job_id: str, body: TerminalCallback, now: float) -> None:
        policy = self._policy(connection, job_id)
        result_expires_at = now + policy.result_retention_days * DAY
        updated = connection.execute(
            """UPDATE video_jobs SET status=?, finished_at=?, result_expires_at=?, tombstone_expires_at=?,
               artifact_gc_after=? WHERE id=? AND status='processing'""",
            (
                "failed" if body.status == "failed" else "completed",
                now,
                result_expires_at,
                result_expires_at + policy.tombstone_retention_days * DAY,
                now + policy.artifact_retention_hours * HOUR,
                job_id,
            ),
        ).rowcount
        if updated != 1:
            raise RuntimeError("job is already terminal")
        # The body is fixed here and never rebuilt; callbacks and GET both serve exactly these bytes.
        connection.execute("INSERT INTO video_results (job_id, body) VALUES (?, ?)", (job_id, _dumps(body.wire())))
        connection.execute(
            """INSERT INTO video_callbacks (job_id, status, reservations, max_attempts, next_at, updated_at)
               VALUES (?, 'pending', 0, ?, ?, ?)""",
            (job_id, policy.callback_max_attempts, now, now),
        )

    # ------------------------------------------------------------------ callback outbox

    def claim_callback(self, capacity: int, lease_seconds: int) -> CallbackClaim | None:
        with self._write() as connection:
            now = self.clock.now()
            self._reclaim_expired_callback_leases(connection)
            sending = connection.execute("SELECT COUNT(*) FROM video_callbacks WHERE status='sending'").fetchone()[0]
            if sending >= capacity:
                return None
            row = connection.execute(
                """SELECT c.job_id, c.reservations, c.max_attempts, j.request, j.policy, r.body
                   FROM video_callbacks c JOIN video_jobs j ON j.id = c.job_id
                   JOIN video_results r ON r.job_id = c.job_id
                   WHERE c.status='pending' AND c.next_at <= ? AND j.result_expires_at > ?
                   ORDER BY c.next_at LIMIT 1""",
                (now, now),
            ).fetchone()
            if row is None:
                return None
            token = uuid.uuid4().hex
            # The reservation is consumed before any HTTP happens: a crash right after this commit may
            # spend one attempt without a request, but restarts can never exceed the budget.
            connection.execute(
                """UPDATE video_callbacks SET status='sending', reservations=reservations+1, token=?, lease_until=?,
                   updated_at=? WHERE job_id=?""",
                (token, now + lease_seconds, now, row["job_id"]),
            )
            policy = AcceptedVideoPolicy.model_validate(json.loads(row["policy"]))
            return CallbackClaim(
                job_id=row["job_id"],
                token=token,
                url=VideoRequest.model_validate(json.loads(row["request"])).callback_url,
                body=json.loads(row["body"]),
                reservation=row["reservations"] + 1,
                max_attempts=row["max_attempts"],
                retry_delays_seconds=policy.callback_retry_delays_seconds,
                deadline_seconds=policy.callback_deadline_seconds,
            )

    def _reclaim_expired_callback_leases(self, connection: sqlite3.Connection) -> None:
        now = self.clock.now()
        rows = connection.execute(
            """SELECT job_id, reservations, max_attempts, lease_until FROM video_callbacks
               WHERE status='sending' AND lease_until <= ?""",
            (now,),
        ).fetchall()
        for row in rows:
            if row["reservations"] >= row["max_attempts"]:
                connection.execute(
                    """UPDATE video_callbacks SET status='failed', token=NULL, lease_until=NULL,
                       last_outcome='lease_expired', updated_at=? WHERE job_id=?""",
                    (now, row["job_id"]),
                )
                continue
            delays = self._policy(connection, row["job_id"]).callback_retry_delays_seconds
            connection.execute(
                """UPDATE video_callbacks SET status='pending', token=NULL, lease_until=NULL,
                   last_outcome='lease_expired', next_at=?, updated_at=? WHERE job_id=?""",
                (row["lease_until"] + delays[row["reservations"] - 1], now, row["job_id"]),
            )

    def finish_callback(self, claim: CallbackClaim, decision: DeliveryDecision, outcome_code: str) -> bool:
        with self._write() as connection:
            now = self.clock.now()
            row = connection.execute(
                """SELECT reservations, max_attempts FROM video_callbacks
                   WHERE job_id=? AND token=? AND status='sending'""",
                (claim.job_id, claim.token),
            ).fetchone()
            if row is None:
                return False  # lease reclaimed or result expired: never recreate the outbox
            if decision == "retry" and row["reservations"] < row["max_attempts"]:
                status = "pending"
                next_at = now + claim.retry_delays_seconds[row["reservations"] - 1]
            else:
                status = "delivered" if decision == "delivered" else "failed"
                next_at = now
            connection.execute(
                """UPDATE video_callbacks SET status=?, next_at=?, token=NULL, lease_until=NULL, last_outcome=?,
                   updated_at=? WHERE job_id=?""",
                (status, next_at, outcome_code, now, claim.job_id),
            )
            return True

    # ------------------------------------------------------------------ retention

    def expire_jobs(self, limit: int) -> int:
        with self._write() as connection:
            now = self.clock.now()
            rows = connection.execute(
                """SELECT id FROM video_jobs WHERE status != 'processing' AND result_expires_at <= ?
                   ORDER BY result_expires_at LIMIT ?""",
                (now, limit),
            ).fetchall()
            for row in rows:
                self._expire_job(connection, row["id"])
            return len(rows)

    @staticmethod
    def _expire_job(connection: sqlite3.Connection, job_id: str) -> None:
        """Replace the job with a minimal tombstone in the caller's transaction.

        Result body, request (with URLs), tasks and the undelivered outbox go together, so no retry can
        recreate a callback for expired data. The tombstone keeps the *original* expiry boundary.
        """
        job = connection.execute(
            """SELECT session_id, request_id, identity_version, fingerprint, tombstone_expires_at
               FROM video_jobs WHERE id=? AND status != 'processing'""",
            (job_id,),
        ).fetchone()
        if job is None:
            return
        connection.execute(
            """INSERT OR REPLACE INTO video_tombstones
               (session_id, request_id, job_id, identity_version, fingerprint, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                job["session_id"],
                job["request_id"],
                job_id,
                job["identity_version"] or IDENTITY_VERSION,
                job["fingerprint"],
                job["tombstone_expires_at"],
            ),
        )
        connection.execute("DELETE FROM video_jobs WHERE id=?", (job_id,))

    def purge_tombstones(self, limit: int) -> int:
        with self._write() as connection:
            now = self.clock.now()
            return connection.execute(
                """DELETE FROM video_tombstones WHERE rowid IN (
                     SELECT rowid FROM video_tombstones WHERE expires_at <= ? ORDER BY expires_at LIMIT ?)""",
                (now, limit),
            ).rowcount

    def artifact_gc_candidates(self, limit: int) -> list[GcCandidate]:
        with self._read() as connection:
            now = self.clock.now()
            rows = connection.execute(
                """SELECT a.id, a.job_id, a.stage, a.temp_path, a.final_path FROM video_artifacts a
                   LEFT JOIN video_jobs j ON j.id = a.job_id
                   WHERE a.state != 'collected'
                   AND (j.id IS NULL OR (j.status != 'processing' AND j.artifact_gc_after <= ?))
                   ORDER BY a.created_at LIMIT ?""",
                (now, limit),
            ).fetchall()
        return [
            GcCandidate(
                id=row["id"], job_id=row["job_id"], stage=row["stage"], paths=(row["temp_path"], row["final_path"])
            )
            for row in rows
        ]

    def mark_artifact_collected(self, artifact_id: str) -> bool:
        with self._write() as connection:
            return (
                connection.execute(
                    "UPDATE video_artifacts SET state='collected', collected_at=? WHERE id=? AND state != 'collected'",
                    (self.clock.now(), artifact_id),
                ).rowcount
                == 1
            )

    def remove_artifact_files(self, candidate: GcCandidate) -> None:
        """Unlink only files inside ``artifact_root/<job>/<stage>/``; never follow symlinked directories."""
        expected = self.artifact_root / candidate.job_id / candidate.stage
        for parent in (self.artifact_root / candidate.job_id, expected):
            if parent.is_symlink():
                raise OSError("refusing to collect through a symlinked directory")
        for raw in candidate.paths:
            path = Path(raw)
            if path.parent != expected:
                raise OSError("artifact path is outside its job directory")
            # unlink removes a symlink itself, never its target.
            path.unlink(missing_ok=True)
        for directory in (expected, expected.parent):
            try:
                directory.rmdir()
            except OSError:
                break  # not empty or already gone

    def record_cleanup(self, stats: CleanupStats) -> None:
        now = self.clock.now()
        with self._write() as connection:
            connection.execute(
                "INSERT INTO video_maintenance VALUES (1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "last_pass_at=excluded.last_pass_at, "
                "last_success_at=MAX(video_maintenance.last_success_at, excluded.last_success_at), "
                "artifact_errors=excluded.artifact_errors "
                "WHERE excluded.last_pass_at >= video_maintenance.last_pass_at",
                (now, now if stats.artifact_errors == 0 else 0, stats.artifact_errors),
            )

    def operational_metrics(self) -> dict[str, float]:
        now = self.clock.now()
        metrics: dict[str, float] = {}
        with self._read() as connection:
            for status in ("processing", "completed", "failed"):
                metrics[f"video_jobs_{status}"] = float(
                    connection.execute("SELECT COUNT(*) FROM video_jobs WHERE status=?", (status,)).fetchone()[0]
                )
            for status in ("pending", "running", "succeeded", "failed", "blocked"):
                metrics[f"video_tasks_{status}"] = float(
                    connection.execute("SELECT COUNT(*) FROM video_tasks WHERE status=?", (status,)).fetchone()[0]
                )
            for status in ("pending", "sending", "delivered", "failed"):
                metrics[f"video_callbacks_{status}"] = float(
                    connection.execute("SELECT COUNT(*) FROM video_callbacks WHERE status=?", (status,)).fetchone()[0]
                )
            oldest = connection.execute("SELECT MIN(created_at) FROM video_jobs WHERE status='processing'").fetchone()[
                0
            ]
            metrics["video_oldest_processing_seconds"] = max(0, now - oldest) if oldest is not None else 0
            metrics["video_disk_reserved_bytes"] = float(
                connection.execute(
                    "SELECT COALESCE(SUM(reserved_bytes),0) FROM video_artifacts WHERE state != 'collected'"
                ).fetchone()[0]
            )
            metrics["video_stage_retries"] = float(
                connection.execute("SELECT COALESCE(SUM(MAX(attempts-1,0)),0) FROM video_tasks").fetchone()[0]
            )
            metrics["video_stage_timeouts"] = float(
                connection.execute("SELECT COUNT(*) FROM video_tasks WHERE error_code='PROCESSING_TIMEOUT'").fetchone()[
                    0
                ]
            )
            maintenance = connection.execute("SELECT * FROM video_maintenance WHERE id=1").fetchone()
            metrics["video_cleanup_last_pass_timestamp_seconds"] = maintenance["last_pass_at"] if maintenance else 0
            metrics["video_cleanup_last_success_timestamp_seconds"] = (
                maintenance["last_success_at"] if maintenance else 0
            )
            metrics["video_cleanup_artifact_errors"] = float(maintenance["artifact_errors"]) if maintenance else 0
        metrics["video_disk_budget_bytes"] = float(self.disk_budget_bytes)
        metrics["video_disk_free_bytes"] = float(shutil.disk_usage(self.artifact_root).free)
        return metrics

    # ------------------------------------------------------------------ test/ops helpers

    def job_row(self, job_id: str) -> dict[str, Any] | None:
        with self._read() as connection:
            row = connection.execute("SELECT * FROM video_jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row is not None else None

    def task_rows(self, job_id: str) -> dict[str, dict[str, Any]]:
        with self._read() as connection:
            rows = connection.execute("SELECT * FROM video_tasks WHERE job_id=?", (job_id,)).fetchall()
        return {row["stage"]: dict(row) for row in rows}

    def callback_row(self, job_id: str) -> dict[str, Any] | None:
        with self._read() as connection:
            row = connection.execute("SELECT * FROM video_callbacks WHERE job_id=?", (job_id,)).fetchone()
        return dict(row) if row is not None else None
