from __future__ import annotations

from typing import Literal

from pydantic import Field, HttpUrl, model_validator

from app.core.common.dto import CamelModel

# 한 번에 재작성할 질문 수 상한. /generate 산출물은 5문항 고정이지만 상한만 여유를 둔다.
MAX_QUESTIONS = 10


class CandidateProfileRequest(CamelModel):
    job_role: str | None = Field(default=None, description="지원 직무/포지션. 질문 표현을 맞추는 데 쓴다.")
    experience_level: str | None = Field(default=None, description="경력 수준. 질문의 깊이·어휘 조절에 쓴다.")
    persona_type: str | None = Field(default=None, description="면접관 페르소나 유형. 어조에만 반영된다.")

    # 셋 다 비어 있으면 재작성할 근거가 없다. 형식이 아니라 의미의 문제라 코어(422)에서 막는다.


class OriginalQuestionRequest(CamelModel):
    id: int = Field(..., ge=1, description="원질문 식별자. 재작성 결과와 매칭하는 키.")
    category: str = Field(..., min_length=1, description="질문 카테고리(tech_choice 등). 프롬프트 맥락 용도.")
    question: str = Field(..., min_length=1, description="원질문 본문. 재작성 대상.")
    expected_answer: str = Field(
        ...,
        min_length=1,
        description="원질문이 확인하려던 것. 재작성 후에도 이걸 그대로 확인할 수 있어야 한다.",
    )
    based_on: list[str] = Field(default_factory=list, description="근거 파일 경로. 프롬프트 맥락 용도.")


class TailorRequest(CamelModel):
    interview_id: str = Field(..., description="면접 식별자. 면접 시작 전이라 세션은 아직 없다.")
    user_id: str = Field(..., description="사용자 식별자")
    profile: CandidateProfileRequest = Field(..., description="면접 사전 정보. 재작성의 개인화 축.")
    questions: list[OriginalQuestionRequest] = Field(
        ...,
        min_length=1,
        max_length=MAX_QUESTIONS,
        description="재작성할 원질문 목록",
    )
    callback_url: HttpUrl = Field(..., description="재작성 완료/실패 시 결과를 POST 로 받을 URL")

    @model_validator(mode="after")
    def _check_unique_ids(self) -> TailorRequest:
        # id 로 재작성 결과를 매칭하므로 중복이 있으면 한쪽이 조용히 덮인다.
        ids = [question.id for question in self.questions]
        if len(set(ids)) != len(ids):
            raise ValueError("questions 의 id 는 서로 달라야 합니다.")
        return self


class TailorJobAccepted(CamelModel):
    job_id: str = Field(..., description="이번 작업의 식별자(UUIDv4). 콜백 페이로드와 매칭에 사용.")
    interview_id: str = Field(..., description="요청에 실려온 면접 식별자를 그대로 되돌려준다.")
    status: Literal["accepted"] = "accepted"
    message: str = "질문 재작성 작업을 시작했습니다. 완료 시 callbackUrl 로 결과를 전송합니다."
