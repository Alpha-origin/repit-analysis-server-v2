"""면접 Q&A 파이프라인 도메인 예외.

파이프라인 단계에서 발생한 사용자 에러(잘못된 입력 등) 를 표현한다.
inbound 라우터에서 이 예외를 잡아 HTTP 응답으로 변환한다(상태 코드 + 한글 메시지).
"""


class PipelineError(Exception):
    """파이프라인 단계에서 발생한 도메인 예외.

    Attributes:
        status_code: HTTP 응답 코드 (예: 422, 403, 500).
        message: 사용자에게 노출할 한글 메시지.
    """

    def __init__(self, status_code: int, message: str) -> None:
        # 부모 Exception 메시지로도 동일 문자열을 넣어 두면 stacktrace 가독성이 좋음.
        super().__init__(message)
        self.status_code = status_code
        self.message = message
