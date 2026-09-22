from urllib.parse import urlsplit

from app.core.common.audio.dto import AudioError


def validate_source_url(url: str, allowed_hosts: tuple[str, ...]) -> None:
    """Only operator-approved S3 endpoints; never follow redirects."""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        allowed = (
            parsed.scheme == "https"
            and host in allowed_hosts
            and host.endswith((".amazonaws.com", ".amazonaws.com.cn"))
            and ".s3" in host
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
            and not parsed.fragment
            and bool(parsed.path.strip("/"))
        )
    except ValueError as exc:
        raise AudioError("invalid_source_url") from exc
    if not allowed:
        raise AudioError("invalid_source_url")
