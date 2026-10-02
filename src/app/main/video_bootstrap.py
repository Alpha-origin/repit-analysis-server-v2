"""Shared construction for the video API, worker and cleanup processes (same paths, same security)."""

from __future__ import annotations

from app.core.common.security.ports import RuntimeSecurityProvider
from app.core.common.video.admission import admit_new_request
from app.core.common.video.dto import VideoRequest
from app.core.common.video.policy import AcceptedVideoPolicy
from app.core.common.video.ports import Admit, Clock
from app.main.config import CallbackSecuritySettings
from app.main.security_bootstrap import runtime_security_from
from app.main.video_config import VideoSettings
from app.outbound.adapters.runtime_security_provider import StaticRuntimeSecurityProvider
from app.outbound.adapters.system_clock import SystemClock
from app.outbound.adapters.video.sqlite_repository import SqliteVideoRepository


def video_security(callback: CallbackSecuritySettings, video: VideoSettings) -> StaticRuntimeSecurityProvider:
    return runtime_security_from(callback, video.blocked_source_hosts)


def video_repository(video: VideoSettings, clock: Clock | None = None) -> SqliteVideoRepository:
    return SqliteVideoRepository(video.database_path, video.artifact_root, clock or SystemClock())


def video_admission(video: VideoSettings, security: RuntimeSecurityProvider) -> Admit:
    def admit(request: VideoRequest) -> AcceptedVideoPolicy:
        snapshot = security.current()
        policy = video.accepted_policy(tuple(sorted(snapshot.allowed_callback_hosts)))
        return admit_new_request(request, policy, snapshot)

    return admit
