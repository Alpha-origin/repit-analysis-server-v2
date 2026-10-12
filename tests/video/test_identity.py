from __future__ import annotations

import pytest

from app.core.common.security.url_policy import UrlPolicyError
from app.core.common.video.identity import canonical_url, request_fingerprint, request_identity
from tests.video.helpers import SIGNED, SOURCE_HOST, file_url, video_request


def test_resign_and_policy_independent_identity() -> None:
    original = video_request()
    resigned = video_request(video={"fileUrl": file_url("second")})
    assert request_fingerprint(original) == request_fingerprint(resigned)
    assert resigned.video.file_url != original.video.file_url  # raw stays distinct for real I/O
    # Pair order, default port, host case and UTC-equivalent offsets do not change identity.
    reordered = f"https://{SOURCE_HOST.upper()}:443/videos/u7/interview.webm?" + "&".join(
        reversed(SIGNED.format(signature="third").split("&"))
    )
    same_instant = video_request(video={"fileUrl": reordered, "uploadedAt": "2026-10-02T00:00:00Z"})
    assert request_fingerprint(same_instant) == request_fingerprint(original)
    # Identity contains request content only (no policy, no secrets).
    assert set(request_identity(original)) == {
        "identityVersion",
        "requestId",
        "sessionId",
        "interviewId",
        "userId",
        "callbackUrl",
        "video",
    }


@pytest.mark.parametrize(
    "changed",
    [
        {"video": {"fileUrl": file_url(extra="&versionId=v2")}},
        {"video": {"fileUrl": file_url(extra="&partNumber=1")}},
        {"video": {"fileUrl": file_url(extra="&response-content-type=video%2Fmp4")}},
        {"video": {"fileUrl": file_url(extra="&X-Amz-Unknown=1")}},  # only documented SigV4 keys are dropped
        {"video": {"fileUrl": file_url().replace("/interview.webm", "/Interview.webm")}},
        {"video": {"fileUrl": file_url().replace("/interview.webm", "/interview%2Ewebm")}},
        {"video": {"fileUrl": file_url().replace("/videos/u7/", "/videos/u7/./")}},
        {"video": {"fileSize": 7}},
        {"video": {"contentType": "video/mp4"}},
        {"video": {"videoId": "video-2"}},
        {"video": {"uploadedAt": "2026-10-02T09:00:01+09:00"}},
        {"callbackUrl": "https://api.repit.example.com/api/analyses/video/callback?tenant=2"},
        {"interviewId": "43"},
        {"userId": "8"},
    ],
)
def test_object_and_response_changes_conflict(changed: dict[str, object]) -> None:
    assert request_fingerprint(video_request(**changed)) != request_fingerprint(video_request())


def test_callback_query_is_never_dropped_and_duplicates_are_ambiguous() -> None:
    url = "https://api.repit.example.com/cb?X-Amz-Signature=keep"
    assert canonical_url(url, drop_sigv4_auth=False) == url
    with pytest.raises(UrlPolicyError, match="duplicate"):
        canonical_url(f"https://{SOURCE_HOST}/v.webm?versionId=1&versionId=2", drop_sigv4_auth=True)
