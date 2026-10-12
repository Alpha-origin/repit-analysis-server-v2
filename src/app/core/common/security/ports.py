from __future__ import annotations

from typing import Any, Protocol

from app.core.common.security.dto import AttemptOutcome, SecuritySnapshot


class RuntimeSecurityProvider(Protocol):
    def current(self) -> SecuritySnapshot: ...


class CallbackTransport(Protocol):
    async def post_once(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        timeout_seconds: float,
        total_deadline_seconds: float | None = None,
        allow_untrusted: bool = False,
    ) -> AttemptOutcome:
        """Send at most one HTTP request.

        Trusted destinations (exact allowed HTTPS host, not blocked) receive ``X-Internal-Token``.
        ``allow_untrusted`` keeps the legacy development behaviour: untrusted destinations are still
        contacted but never receive the token. Blocked hosts are never contacted.
        """
        ...
