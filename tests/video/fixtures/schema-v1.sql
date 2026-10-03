
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
    collected_at REAL
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

PRAGMA user_version=1;
