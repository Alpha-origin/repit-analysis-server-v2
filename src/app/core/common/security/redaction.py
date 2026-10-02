"""Remove credentials from anything that might reach a log line."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s'\"<>]+")
_RELATIVE_QUERY = re.compile(r"(?<![A-Za-z0-9])(/[^\s'\"<>?]*)\?[^\s'\"<>]*")
_SECRET_PAIR = re.compile(
    r"(?i)(x-amz-[a-z-]+|signature|awsaccesskeyid|x-internal-token|token|api[_-]?key|authorization)"
    r"(\s*[=:]\s*)([^\s&'\",;]+)"
)
REDACTED = "[redacted]"


def redact_url(url: str) -> str:
    """Keep scheme, host and path; drop userinfo, port details, query and fragment."""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
    except ValueError:
        return REDACTED
    if not parsed.scheme or not host:
        return REDACTED
    suffix = "?[redacted]" if parsed.query else ""
    return f"{parsed.scheme}://{host}{parsed.path}{suffix}"


def redact_text(text: str) -> str:
    text = _URL.sub(lambda match: redact_url(match.group(0)), text)
    text = _RELATIVE_QUERY.sub(lambda match: f"{match.group(1)}?[redacted]", text)
    return _SECRET_PAIR.sub(lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", text)
