from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Environment = Literal["development", "test", "production"]


@dataclass(frozen=True)
class SecuritySnapshot:
    """Credentials and destination policy of the *running* process, read right before network I/O.

    ``callback_token`` is a plain ``str`` only inside this object; it is never persisted, logged or
    included in job policy. ``repr`` hides it.
    """

    environment: Environment
    callback_token: str | None = field(repr=False)
    allowed_callback_hosts: frozenset[str]
    blocked_callback_hosts: frozenset[str]
    blocked_source_hosts: frozenset[str]


AttemptKind = Literal["delivered", "http_status", "network", "timeout", "rejected"]


@dataclass(frozen=True)
class AttemptOutcome:
    """Result of exactly one HTTP request (or zero, when ``rejected`` before sending)."""

    kind: AttemptKind
    status_code: int | None = None
    reason: str | None = None  # safe classification only (e.g. "host_not_allowed"); never URLs or bodies
