"""Replay identity for video requests.

Only request *content* is compared — never the current or accepted server policy — so a policy deploy
cannot turn a lost-202 retry into a conflict. The raw request (and raw URLs) are stored separately and
are the only thing used for real network I/O.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime

from app.core.common.security.url_policy import parse_https_url, query_pairs
from app.core.common.video.dto import VideoRequest

IDENTITY_VERSION = "video-identity-v1"

# SigV4 presigned-URL authentication parameters (exact names). Re-signing the same object only
# changes these. Everything else — versionId, partNumber, response-*, unknown X-Amz-* — is content.
SIGV4_AUTH_KEYS = frozenset(
    {
        "X-Amz-Algorithm",
        "X-Amz-Credential",
        "X-Amz-Date",
        "X-Amz-Expires",
        "X-Amz-SignedHeaders",
        "X-Amz-Signature",
        "X-Amz-Security-Token",
    }
)


def canonical_url(url: str, *, drop_sigv4_auth: bool) -> str:
    parsed = parse_https_url(url)
    pairs = query_pairs(parsed.query)
    if drop_sigv4_auth:
        pairs = [(key, value) for key, value in pairs if key not in SIGV4_AUTH_KEYS]
    # Path and percent-encoding are kept byte-for-byte; only pair order is normalized.
    query = "&".join(f"{key}={value}" for key, value in sorted(pairs))
    return f"https://{parsed.host}{parsed.path}" + (f"?{query}" if query else "")


def canonical_instant(value: str) -> str:
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return instant.astimezone(UTC).isoformat()


def _identity_v1(request: VideoRequest) -> dict[str, object]:
    return {
        "identityVersion": "video-identity-v1",
        "requestId": request.request_id,
        "sessionId": request.session_id,
        "interviewId": request.interview_id,
        "userId": request.user_id,
        "callbackUrl": canonical_url(request.callback_url, drop_sigv4_auth=False),
        "video": {
            "videoId": request.video.video_id,
            "fileUrl": canonical_url(request.video.file_url, drop_sigv4_auth=True),
            "contentType": request.video.content_type,
            "fileSize": request.video.file_size,
            "uploadedAt": canonical_instant(request.video.uploaded_at),
        },
    }


IDENTITY_BUILDERS: dict[str, Callable[[VideoRequest], dict[str, object]]] = {"video-identity-v1": _identity_v1}


class UnsupportedIdentityVersionError(ValueError):
    pass


def request_identity(request: VideoRequest, version: str = IDENTITY_VERSION) -> dict[str, object]:
    try:
        builder = IDENTITY_BUILDERS[version]
    except KeyError as exc:
        raise UnsupportedIdentityVersionError("unsupported video identity version") from exc
    return builder(request)


def request_fingerprint(request: VideoRequest, version: str = IDENTITY_VERSION) -> str:
    encoded = json.dumps(request_identity(request, version), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()
