from __future__ import annotations

from typing import Literal, get_args

PublicErrorCode = Literal[
    "SOURCE_ACCESS_DENIED_OR_EXPIRED",
    "SOURCE_DOWNLOAD_FAILED",
    "EXTERNAL_SERVICE_UNAVAILABLE",
    "PROCESSING_TIMEOUT",
    "SOURCE_NOT_FOUND",
    "SOURCE_SIZE_MISMATCH",
    "VIDEO_LIMIT_EXCEEDED",
    "VIDEO_FORMAT_UNSUPPORTED",
    "VIDEO_DECODE_FAILED",
    "INTERNAL_ERROR",
    "ANALYZER_NOT_CONFIGURED",
]

PUBLIC_CODES: frozenset[str] = frozenset(get_args(PublicErrorCode))

# retryable 은 "같은 영상을 새 requestId 로 다시 요청하면 복구될 수 있다"는 뜻이다.
# 서버 내부 단계 재시도 여부(VideoStageError.stage_retry)와는 별개다.
RETRYABLE: dict[str, bool] = {
    "SOURCE_ACCESS_DENIED_OR_EXPIRED": True,
    "SOURCE_DOWNLOAD_FAILED": True,
    "EXTERNAL_SERVICE_UNAVAILABLE": True,
    "PROCESSING_TIMEOUT": True,
    "SOURCE_NOT_FOUND": False,
    "SOURCE_SIZE_MISMATCH": False,
    "VIDEO_LIMIT_EXCEEDED": False,
    "VIDEO_FORMAT_UNSUPPORTED": False,
    "VIDEO_DECODE_FAILED": False,
    "INTERNAL_ERROR": False,
    "ANALYZER_NOT_CONFIGURED": False,
}

MESSAGES: dict[str, str] = {
    "SOURCE_ACCESS_DENIED_OR_EXPIRED": "영상 파일에 접근할 수 없습니다. 새 다운로드 URL 로 다시 요청해 주세요.",
    "SOURCE_DOWNLOAD_FAILED": "영상 파일을 내려받지 못했습니다.",
    "EXTERNAL_SERVICE_UNAVAILABLE": "외부 서비스를 일시적으로 사용할 수 없습니다.",
    "PROCESSING_TIMEOUT": "영상 처리 시간이 제한을 넘었습니다.",
    "SOURCE_NOT_FOUND": "영상 파일을 찾을 수 없습니다.",
    "SOURCE_SIZE_MISMATCH": "영상 파일 크기가 요청한 크기와 다릅니다.",
    "VIDEO_LIMIT_EXCEEDED": "영상의 크기·길이·해상도·프레임률 제한을 넘었습니다.",
    "VIDEO_FORMAT_UNSUPPORTED": "지원하지 않는 영상 형식입니다. WebM(VP8/VP9) 또는 MP4(H.264)만 지원합니다.",
    "VIDEO_DECODE_FAILED": "영상 파일이 손상되었거나 해석할 수 없습니다.",
    "INTERNAL_ERROR": "영상 분석 서버 내부 오류가 발생했습니다.",
    "ANALYZER_NOT_CONFIGURED": "영상 행동 분석기가 아직 연결되지 않았습니다.",
}

assert set(RETRYABLE) == PUBLIC_CODES == set(MESSAGES)  # noqa: S101 — import-time table consistency


class VideoStageError(Exception):
    """A typed stage failure.

    ``code`` is the public cause exposed in callbacks. ``stage_retry`` only decides whether this server
    retries the same stage within the accepted attempt budget; it never leaks into the public payload.
    """

    def __init__(self, code: PublicErrorCode, *, stage_retry: bool = False) -> None:
        super().__init__(code)
        self.code: PublicErrorCode = code
        self.stage_retry = stage_retry
