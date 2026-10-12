from __future__ import annotations

from app.core.common.security.dto import SecuritySnapshot
from app.core.common.security.url_policy import UrlPolicyError, check_callback_url, check_source_url
from app.core.common.video.dto import VideoRequest
from app.core.common.video.policy import AcceptedVideoPolicy
from app.core.common.video.ports import AdmissionError


def admit_new_request(
    request: VideoRequest, policy: AcceptedVideoPolicy, security: SecuritySnapshot
) -> AcceptedVideoPolicy:
    """Current-policy checks for a request that is not a replay. Replays never reach this function."""
    limits = policy.limits
    if request.video.file_size > limits.max_bytes:
        raise AdmissionError("video_too_large")
    try:
        check_source_url(request.video.file_url, frozenset(limits.source_hosts), security.blocked_source_hosts)
    except UrlPolicyError as exc:
        raise AdmissionError("source_url_not_allowed") from exc
    try:
        check_callback_url(request.callback_url, security.allowed_callback_hosts, security.blocked_callback_hosts)
    except UrlPolicyError as exc:
        raise AdmissionError("callback_url_not_allowed") from exc
    return policy
