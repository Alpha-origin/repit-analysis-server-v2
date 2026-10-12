from __future__ import annotations

from typing import Literal

from pydantic import Field, HttpUrl

from app.core.common.applicant_profile.dto import ApplicantProfile
from app.core.common.dto import CamelModel
from app.core.common.question_cycle.dto import CycleMode


class QuestionCycleHttpRequest(CamelModel):
    mode: CycleMode = Field(..., description="SOLO(1:1, 15문항) 또는 MULTI(N:1 기술 면접관, 6문항)")
    # /profile 콜백의 result.profile 을 그대로 되돌려받는다. 형태가 콜백과 한 몸이어야 해서
    # HTTP 용 사본을 따로 두지 않고 코어 모델을 그대로 쓴다(사본이 어긋나면 되돌려받을 때 깨진다).
    # 버전·근거 검증은 형식이 아니라 의미의 문제라 코어에서 하고 실패 콜백 422 로 알린다.
    profile: ApplicantProfile = Field(..., description="/profile 결과의 profile")
    exclude_questions: list[str] = Field(
        default_factory=list,
        description="같은 모드의 최근 2사이클 질문 본문. 최대 30개까지만 쓰고 나머지는 버린다.",
    )
    callback_url: HttpUrl = Field(..., description="완료/실패 시 결과를 POST 로 받을 URL")


class QuestionCycleJobAccepted(CamelModel):
    job_id: str = Field(..., description="이번 작업의 식별자(UUIDv4). 콜백 페이로드와 매칭에 사용.")
    status: Literal["accepted"] = "accepted"
    message: str = "질문 사이클 생성 작업을 시작했습니다. 완료 시 callbackUrl 로 결과를 전송합니다."
