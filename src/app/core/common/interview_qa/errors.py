

class PipelineError(Exception):

    def __init__(self, status_code: int, message: str) -> None:
        # 부모 Exception 메시지로도 동일 문자열을 넣어 두면 stacktrace 가독성이 좋음.
        super().__init__(message)
        self.status_code = status_code
        self.message = message
