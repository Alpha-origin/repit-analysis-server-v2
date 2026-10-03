from __future__ import annotations

from app.core.common.security.dto import Environment, SecuritySnapshot
from app.core.common.security.url_policy import normalize_hosts


class StaticRuntimeSecurityProvider:
    """Security values of this process, fixed at boot.

    Changing credentials or block lists means replacing every API/worker/cleanup process; there is no
    hot reload. Jobs never store these values, so restarted processes apply them to old jobs too.
    """

    def __init__(
        self,
        *,
        environment: Environment,
        callback_token: str | None,
        allowed_callback_hosts: tuple[str, ...] = (),
        blocked_callback_hosts: tuple[str, ...] = (),
        blocked_source_hosts: tuple[str, ...] = (),
    ) -> None:
        self._snapshot = SecuritySnapshot(
            environment=environment,
            callback_token=callback_token,
            allowed_callback_hosts=normalize_hosts(allowed_callback_hosts),
            blocked_callback_hosts=normalize_hosts(blocked_callback_hosts),
            blocked_source_hosts=normalize_hosts(blocked_source_hosts),
        )

    def current(self) -> SecuritySnapshot:
        return self._snapshot


DEVELOPMENT_DEFAULT = StaticRuntimeSecurityProvider(environment="development", callback_token=None)
