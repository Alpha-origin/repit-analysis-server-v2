from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    # 소켓/API 서버는 camelCase 를 쓰고 파이썬은 snake_case 를 쓴다.
    # alias_generator 로 두 표현을 잇고, populate_by_name 으로 파이썬 이름 생성도 허용한다.
    # 이 모델을 상속한 응답은 반드시 model_dump(by_alias=True) 로 덤프해야 camelCase 로 나간다.
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


# ===== 콜백 페이로드 (1:1 / N:1 공용) =====


class FeedbackErrorDetail(CamelModel):
    status_code: int  # 422(채점 대상 없음), 502(LLM 호출 실패), 500(내부 오류) 등
    message: str  # 사용자에게 노출 가능한 한글 메시지


class FeedbackCallbackFailure(CamelModel):
    job_id: str
    # 실패 페이로드에는 result 가 없어서, session_id 가 없으면 수신측이 어느 세션인지 알 수 없다.
    session_id: str
    status: Literal["failed"] = "failed"
    error: FeedbackErrorDetail
