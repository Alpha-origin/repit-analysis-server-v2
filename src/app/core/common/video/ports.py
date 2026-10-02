from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from app.core.common.video.dto import AnalysisOutcome, JobSnapshot, PreparedVideo, TerminalCallback, VideoRequest
from app.core.common.video.errors import PublicErrorCode
from app.core.common.video.policy import AcceptedVideoPolicy, VideoLimits

Stage = Literal["source", "inspect", "validate", "analyze", "finalize"]
STAGES: tuple[Stage, ...] = ("source", "inspect", "validate", "analyze", "finalize")
# finalize 는 차단·실패를 포함한 모든 선행 단계의 종료를 기다린다.
DEPENDENCIES: dict[Stage, tuple[Stage, ...]] = {
    "source": (),
    "inspect": ("source",),
    "validate": ("inspect",),
    "analyze": ("validate",),
    "finalize": ("source", "inspect", "validate", "analyze"),
}
# Hour-long decodes occupy the cpu lane; finalize is DB-only and must not wait behind them.
LANES: dict[Stage, str] = {
    "source": "io",
    "inspect": "cpu",
    "validate": "cpu",
    "analyze": "cpu",
    "finalize": "io",
}


class Clock(Protocol):
    def now(self) -> float:
        """UTC epoch seconds."""
        ...


class VideoAnalyzer(Protocol):
    async def analyze(self, prepared: PreparedVideo) -> AnalysisOutcome: ...


# ---------------------------------------------------------------- repository value objects


class AdmissionError(Exception):
    """A *new* request violates the current admission policy (422)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class RepositoryUnavailableError(Exception):
    """Storage is temporarily unavailable (locked/busy disk). Nothing was persisted."""


class ReplayConflictError(Exception):
    """Same (sessionId, requestId) with different content (409)."""


class ReplayGoneError(Exception):
    """Same request whose result already expired; only its tombstone remains (410)."""


@dataclass(frozen=True)
class Accepted:
    job_id: str
    created: bool


Admit = Callable[[VideoRequest], AcceptedVideoPolicy]


@dataclass(frozen=True)
class SnapshotFound:
    snapshot: JobSnapshot


@dataclass(frozen=True)
class SnapshotGone:
    pass


@dataclass(frozen=True)
class SnapshotMissing:
    pass


SnapshotOutcome = SnapshotFound | SnapshotGone | SnapshotMissing


@dataclass(frozen=True)
class StageTask:
    id: str
    job_id: str
    stage: Stage
    token: str
    attempts: int


@dataclass(frozen=True)
class StageRecord:
    stage: Stage
    status: str
    result: dict[str, Any] | None
    error_code: PublicErrorCode | None


@dataclass(frozen=True)
class StageContext:
    request: VideoRequest
    policy: AcceptedVideoPolicy
    dependencies: dict[Stage, StageRecord]


@dataclass(frozen=True)
class ArtifactReservation:
    id: str
    temp_path: str
    final_path: str


@dataclass(frozen=True)
class StageOutput:
    """Successful stage data plus temporary files whose ownership passes to the repository on finish."""

    data: dict[str, Any]
    staged_artifacts: tuple[ArtifactReservation, ...] = field(default=())


@dataclass(frozen=True)
class CallbackClaim:
    job_id: str
    token: str
    url: str
    body: dict[str, Any]
    reservation: int
    max_attempts: int
    retry_delays_seconds: tuple[int, ...]
    deadline_seconds: int


DeliveryDecision = Literal["delivered", "retry", "failed"]


@dataclass(frozen=True)
class GcCandidate:
    id: str
    job_id: str
    stage: str
    paths: tuple[str, str]


@dataclass(frozen=True)
class CleanupStats:
    expired_jobs: int = 0
    purged_tombstones: int = 0
    collected_artifacts: int = 0
    artifact_errors: int = 0


class VideoRepository(Protocol):
    def submit(self, request: VideoRequest, fingerprint: str, admit: Admit) -> Accepted: ...

    def snapshot(self, job_id: str) -> SnapshotOutcome: ...

    def claim_stage(self, capacities: dict[str, int], lease_seconds: int) -> StageTask | None: ...

    def heartbeat(self, task: StageTask, lease_seconds: int) -> bool: ...

    def stage_context(self, task: StageTask) -> StageContext: ...

    def reserve_artifact(self, task: StageTask, suffix: str) -> ArtifactReservation | None: ...

    def finish_stage(self, task: StageTask, output: StageOutput) -> bool: ...

    def block_stage(self, task: StageTask, cause: PublicErrorCode) -> bool: ...

    def fail_stage(self, task: StageTask, code: PublicErrorCode, *, stage_retry: bool) -> bool: ...

    def terminalize(self, task: StageTask, body: TerminalCallback) -> bool: ...

    def claim_callback(self, capacity: int, lease_seconds: int) -> CallbackClaim | None: ...

    def finish_callback(self, claim: CallbackClaim, decision: DeliveryDecision, outcome_code: str) -> bool: ...


class RetentionRepository(Protocol):
    def expire_jobs(self, limit: int) -> int: ...

    def purge_tombstones(self, limit: int) -> int: ...

    def artifact_gc_candidates(self, limit: int) -> list[GcCandidate]: ...

    def remove_artifact_files(self, candidate: GcCandidate) -> None: ...

    def mark_artifact_collected(self, artifact_id: str) -> bool: ...


class VideoMediaBackend(Protocol):
    async def download(
        self, request: VideoRequest, limits: VideoLimits, reservation: ArtifactReservation
    ) -> dict[str, Any]: ...

    async def inspect(self, path: str, limits: VideoLimits) -> dict[str, Any]: ...

    async def validate(self, path: str, inspected: dict[str, Any], limits: VideoLimits) -> dict[str, Any]: ...
