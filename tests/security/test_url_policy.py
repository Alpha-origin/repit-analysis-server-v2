from __future__ import annotations

import pytest

from app.core.common.security.url_policy import (
    UrlPolicyError,
    check_callback_url,
    check_source_url,
    is_aws_s3_endpoint,
)

CALLBACK = frozenset({"api.repit.example.com"})
SOURCE = frozenset({"bucket.s3.ap-northeast-2.amazonaws.com", "files.example.com"})
NONE: frozenset[str] = frozenset()


def test_exact_allowed_https_destination() -> None:
    assert check_callback_url("https://api.repit.example.com/cb", CALLBACK, NONE).host == "api.repit.example.com"
    assert check_callback_url("https://API.repit.example.com:443/cb?x=1", CALLBACK, NONE).query == "x=1"
    assert check_source_url("https://bucket.s3.ap-northeast-2.amazonaws.com/a.webm?X=1", SOURCE, NONE).query == "X=1"
    # Callbacks have no S3 shape requirement; sources need both exact listing and S3 shape.
    with pytest.raises(UrlPolicyError, match="not_s3_endpoint"):
        check_source_url("https://files.example.com/a.webm", SOURCE, NONE)
    with pytest.raises(UrlPolicyError, match="blocked_host"):
        check_callback_url("https://api.repit.example.com/cb", CALLBACK, CALLBACK)


@pytest.mark.parametrize(
    "url",
    [
        "http://api.repit.example.com/cb",
        "HTTPS://api.repit.example.com/cb",
        "https://api.repit.example.com:8443/cb",
        "https://api.repit.example.com:/cb",  # empty explicit port
        "https://api.repit.example.com:0443/cb",
        "https://api.repit.example.com/cb#",  # empty fragment
        "https://api.repit.example.com/cb#frag",
        "https://user@api.repit.example.com/cb",
        "https://user:pw@api.repit.example.com/cb",
        "https://api.repit.example.com@evil.example.com/cb",
        "https://evil.example.com\\@api.repit.example.com/cb",
        "https://api.repit.example.com./cb",  # trailing dot
        "https://api.repit.example.com.evil.example.com/cb",
        "https://evil-api.repit.example.com/cb",
        "https://127.0.0.1/cb",
        "https://[::1]/cb",
        "https://2130706433/cb",
        "https://0x7f.1/cb",
        "https://127.1/cb",
        "https://api.repit.examplé.com/cb",  # non-ASCII / IDNA ambiguity
        "https://xn--api-repit.example.com/cb",
        "https://api.repit.example.com/c b",
        "https://api.repit.example.com/cb\x00",
        "https://api.repit.example.com/cb\n",
        " https://api.repit.example.com/cb",
        "",
    ],
)
def test_destination_bypass_matrix(url: str) -> None:
    with pytest.raises(UrlPolicyError):
        check_callback_url(url, CALLBACK, NONE)


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("bucket.s3.ap-northeast-2.amazonaws.com", True),
        ("bucket.s3.amazonaws.com", True),
        ("s3.ap-northeast-2.amazonaws.com", True),
        ("bucket.s3-ap-northeast-2.amazonaws.com", True),
        ("bucket.s3.dualstack.ap-northeast-2.amazonaws.com", True),
        ("bucket.s3.cn-north-1.amazonaws.com.cn", True),
        ("amazonaws.com", False),
        ("bucket.s3.amazonaws.com.evil.example.com", False),
        ("s3.example.com", False),
        ("bucket.ec2.amazonaws.com", False),
    ],
)
def test_aws_s3_endpoint_shape(host: str, expected: bool) -> None:
    assert is_aws_s3_endpoint(host) is expected


def test_source_needs_object_path() -> None:
    with pytest.raises(UrlPolicyError, match="missing_object_path"):
        check_source_url("https://bucket.s3.ap-northeast-2.amazonaws.com/", SOURCE, NONE)
