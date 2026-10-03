"""Build the process-wide callback security provider (kept free of model/SDK imports)."""

from __future__ import annotations

from app.main.config import CallbackSecuritySettings
from app.outbound.adapters.runtime_security_provider import StaticRuntimeSecurityProvider


def runtime_security_from(
    settings: CallbackSecuritySettings, blocked_source_hosts: tuple[str, ...] = ()
) -> StaticRuntimeSecurityProvider:
    token = settings.INTERNAL_CALLBACK_TOKEN
    return StaticRuntimeSecurityProvider(
        environment=settings.ENVIRONMENT,
        callback_token=token.get_secret_value() if token is not None else None,
        allowed_callback_hosts=settings.CALLBACK_ALLOWED_HOSTS,
        blocked_callback_hosts=settings.CALLBACK_BLOCKED_HOSTS,
        blocked_source_hosts=blocked_source_hosts,
    )
