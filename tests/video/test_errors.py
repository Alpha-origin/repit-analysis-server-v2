from __future__ import annotations

from pathlib import Path

import pytest

from app.core.common.video.dto import PublicError, public_error
from app.core.common.video.errors import PUBLIC_CODES, RETRYABLE, VideoStageError
from app.core.common.video.policy import VideoLimits
from app.outbound.adapters.video.media_backend import LocalVideoMediaBackend
from tests.video.helpers import security


def test_public_code_retryable_table() -> None:
    assert {code for code, retryable in RETRYABLE.items() if retryable} == {
        "SOURCE_ACCESS_DENIED_OR_EXPIRED",
        "SOURCE_DOWNLOAD_FAILED",
        "EXTERNAL_SERVICE_UNAVAILABLE",
        "PROCESSING_TIMEOUT",
    }
    assert {code for code, retryable in RETRYABLE.items() if not retryable} == {
        "SOURCE_NOT_FOUND",
        "SOURCE_SIZE_MISMATCH",
        "VIDEO_LIMIT_EXCEEDED",
        "VIDEO_FORMAT_UNSUPPORTED",
        "VIDEO_DECODE_FAILED",
        "INTERNAL_ERROR",
        "ANALYZER_NOT_CONFIGURED",
    }
    for code in PUBLIC_CODES:
        error = public_error(code)  # type: ignore[arg-type]
        assert error.wire() == {"code": code, "message": error.message, "retryable": RETRYABLE[code]}
        assert not any(marker in error.message.lower() for marker in ("var/", "/private/", "ffprobe", "traceback"))


def test_public_error_rejects_unknown_codes_and_wrong_retryable() -> None:
    with pytest.raises(ValueError, match="code"):
        PublicError.model_validate({"code": "FFPROBE_CRASHED", "message": "x", "retryable": False})
    with pytest.raises(ValueError, match="retryable"):
        PublicError.model_validate({"code": "VIDEO_DECODE_FAILED", "message": "x", "retryable": True})


@pytest.mark.asyncio
async def test_tool_failure_is_not_decode_failure(tmp_path: Path) -> None:
    backend = LocalVideoMediaBackend(security(), ffprobe=str(tmp_path / "missing-ffprobe"))
    webm = tmp_path / "header-ok.webm"
    webm.write_bytes(b"\x1a\x45\xdf\xa3\x87\x42\x82\x84webm")
    with pytest.raises(VideoStageError) as caught:
        await backend.inspect(str(webm), VideoLimits())
    assert caught.value.code == "INTERNAL_ERROR"  # never reported as VIDEO_DECODE_FAILED
