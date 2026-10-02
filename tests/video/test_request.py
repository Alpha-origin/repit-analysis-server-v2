from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from app.inbound.http.video.dto import VideoAnalysisRequest
from tests.video.helpers import SOURCE_HOST, payload


def test_valid_request() -> None:
    body = payload()
    parsed = VideoAnalysisRequest.model_validate(body)
    request = parsed.to_request()
    assert request.video.file_url == body["video"]["fileUrl"]  # raw URL preserved byte-for-byte
    assert request.wire() == body
    # Policy-independent: a 5GB declared size is structurally valid; admission decides for new jobs.
    huge = payload(video={"fileSize": 5_000_000_000})
    assert VideoAnalysisRequest.model_validate(huge).video.file_size == 5_000_000_000
    padded = payload(requestId=" req-1 ")
    assert VideoAnalysisRequest.model_validate(padded).request_id == " req-1 "  # never trimmed


def _mutate(path: list[str], value: Any) -> Callable[[dict[str, Any]], None]:
    def apply(body: dict[str, Any]) -> None:
        target = body
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return apply


def _delete(path: list[str]) -> Callable[[dict[str, Any]], None]:
    def apply(body: dict[str, Any]) -> None:
        target = body
        for key in path[:-1]:
            target = target[key]
        del target[path[-1]]

    return apply


def _snake(body: dict[str, Any]) -> None:
    body["request_id"] = body.pop("requestId")


@pytest.mark.parametrize(
    "mutation",
    [
        _mutate(["video", "fileSize"], "6"),
        _mutate(["video", "fileSize"], 6.0),
        _mutate(["video", "fileSize"], True),
        _mutate(["video", "fileSize"], 0),
        _mutate(["video", "fileSize"], -1),
        _mutate(["video", "uploadedAt"], "2026-10-02T09:00:00"),  # naive
        _mutate(["video", "uploadedAt"], 1_790_000_000),  # epoch number
        _mutate(["video", "uploadedAt"], "1790000000"),
        _mutate(["video", "uploadedAt"], "2026-10-02"),
        _mutate(["requestId"], "   "),
        _mutate(["requestId"], ""),
        _mutate(["requestId"], 1),
        _mutate(["requestId"], "x" * 201),
        _mutate(["video"], [payload()["video"]]),  # array instead of one video
        _mutate(["video", "extra"], 1),
        _mutate(["extra"], 1),
        _mutate(["video", "contentType"], "audio/webm"),
        _mutate(["video", "contentType"], "video"),
        _mutate(["video", "fileUrl"], f"http://{SOURCE_HOST}/v.webm"),
        _mutate(["video", "fileUrl"], f"https://user@{SOURCE_HOST}/v.webm"),
        _mutate(["video", "fileUrl"], f"https://{SOURCE_HOST}/v.webm#"),
        _mutate(["video", "fileUrl"], f"https://{SOURCE_HOST}/v.webm?a=1&a=2"),
        _mutate(["callbackUrl"], "https://api.repit.example.com:8443/cb"),
        _mutate(["callbackUrl"], "https://api.repit.example.com:/cb"),
        _delete(["callbackUrl"]),
        _delete(["video", "videoId"]),
        _snake,
    ],
)
def test_strict_wire_rejections(mutation: Callable[[dict[str, Any]], None]) -> None:
    body = copy.deepcopy(payload())
    mutation(body)
    with pytest.raises(ValidationError) as caught:
        VideoAnalysisRequest.model_validate(body)
    # Errors never echo the input (it may contain a signed URL).
    assert "X-Amz-Signature" not in str(caught.value)
