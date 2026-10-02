from __future__ import annotations

import hmac

from fastapi import Request
from pydantic import SecretStr

TOKEN_HEADER = "x-internal-token"  # noqa: S105 — header name, not a secret


def is_authenticated(request: Request, expected: SecretStr) -> bool:
    """Constant-time comparison of ``X-Internal-Token``. Runs before any body parsing or lookup."""
    provided = request.headers.get(TOKEN_HEADER)
    secret = expected.get_secret_value()
    if provided is None or not secret:
        return False
    return hmac.compare_digest(provided.encode(), secret.encode())
