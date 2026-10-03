"""Exact-host HTTPS destination checks shared by source downloads and callbacks.

The parser is deliberately stricter than ``urllib``: anything two parsers could read differently
(userinfo, IP literals, trailing dots, non-ASCII hosts, empty ports/fragments) is rejected.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_HEX_OR_DIGITS = re.compile(r"^(0x[0-9a-f]*|[0-9]+)$")
# Virtual-hosted (<bucket>.s3.<region>.amazonaws.com), path-style (s3.<region>.amazonaws.com),
# legacy dash-region (s3-<region>) and dualstack endpoints. Matching this is necessary but never
# sufficient: the exact host must also be on the operator's list.
_AWS_S3 = re.compile(r"^([a-z0-9][a-z0-9.-]*\.)?s3([.-][a-z0-9-]+)*\.amazonaws\.com(\.cn)?$")
MAX_URL_LENGTH = 16384


class UrlPolicyError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class HttpsUrl:
    host: str
    path: str
    query: str


def parse_https_url(url: str) -> HttpsUrl:
    if not url or len(url) > MAX_URL_LENGTH:
        raise UrlPolicyError("invalid_length")
    if any(ord(char) <= 0x20 or ord(char) == 0x7F or char == "\\" for char in url):  # noqa: PLR2004
        raise UrlPolicyError("control_space_or_backslash")
    if not url.isascii():
        raise UrlPolicyError("non_ascii")
    if "#" in url:
        raise UrlPolicyError("fragment")
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise UrlPolicyError("unparseable") from exc
    if parsed.scheme != "https" or not url.startswith("https://"):
        raise UrlPolicyError("not_https")
    netloc = parsed.netloc
    if "@" in netloc:
        raise UrlPolicyError("userinfo")
    if netloc.startswith("["):
        raise UrlPolicyError("ip_literal")
    host, separator, port = netloc.partition(":")
    if separator and port != "443":
        raise UrlPolicyError("port")
    host = host.lower()
    validate_hostname(host)
    return HttpsUrl(host=host, path=parsed.path, query=parsed.query)


def validate_hostname(host: str) -> None:
    if not host or len(host) > 253:  # noqa: PLR2004 — DNS name length
        raise UrlPolicyError("host")
    if host.endswith("."):
        raise UrlPolicyError("trailing_dot")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise UrlPolicyError("ip_literal")
    labels = host.split(".")
    if len(labels) < 2 or any(not _LABEL.match(label) for label in labels):  # noqa: PLR2004
        raise UrlPolicyError("host")
    if _HEX_OR_DIGITS.match(labels[-1]):
        # inet_aton style shorthands such as 127.1 or 0x7f.1 resolve to IP addresses.
        raise UrlPolicyError("ip_literal")


def normalize_hosts(hosts: tuple[str, ...] | list[str]) -> frozenset[str]:
    normalized = set()
    for host in hosts:
        validate_hostname(host)
        normalized.add(host)
    return frozenset(normalized)


def is_aws_s3_endpoint(host: str) -> bool:
    return bool(_AWS_S3.match(host))


def check_source_url(url: str, allowed_hosts: frozenset[str], blocked_hosts: frozenset[str]) -> HttpsUrl:
    parsed = parse_https_url(url)
    if parsed.host in blocked_hosts:
        raise UrlPolicyError("blocked_host")
    if parsed.host not in allowed_hosts:
        raise UrlPolicyError("host_not_allowed")
    if not is_aws_s3_endpoint(parsed.host):
        raise UrlPolicyError("not_s3_endpoint")
    if not parsed.path.strip("/"):
        raise UrlPolicyError("missing_object_path")
    return parsed


def check_callback_url(url: str, allowed_hosts: frozenset[str], blocked_hosts: frozenset[str]) -> HttpsUrl:
    parsed = parse_https_url(url)
    if parsed.host in blocked_hosts:
        raise UrlPolicyError("blocked_host")
    if parsed.host not in allowed_hosts:
        raise UrlPolicyError("host_not_allowed")
    return parsed


def query_pairs(query: str) -> list[tuple[str, str]]:
    """Split a raw query without decoding it. Duplicate keys are ambiguous and rejected."""
    if not query:
        return []
    pairs = []
    seen = set()
    for item in query.split("&"):
        key, _, value = item.partition("=")
        if not key:
            raise UrlPolicyError("empty_query_key")
        if key in seen:
            raise UrlPolicyError("duplicate_query_key")
        seen.add(key)
        pairs.append((key, value))
    return pairs
