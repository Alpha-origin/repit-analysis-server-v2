from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.core.common.dto import CamelModel

# ===== 작업 입력 (Command 진입점) =====


class CandidateProfile(BaseModel):
    # 재작성의 개인화 축. 셋 다 비어 있으면 재작성할 근거가 없어 422 로 막는다.
    job_role: str | None = None  # 지원 직무/포지션 (백엔드, 프론트엔드 등)
    experience_level: str | None = None  # 경력 수준 (신입/주니어/시니어 등)
    persona_type: str | None = None  # 면접관 페르소나. 어조에만 반영한다.

    @property
    def has_any(self) -> bool:
        return any(
            value is not None and value.strip() for value in (self.job_role, self.experience_level, self.persona_type)
        )


class OriginalQuestion(BaseModel):
    # /generate 산출물(InterviewItem) 을 그대로 되받은 형태.
    id: int
    # category 는 프롬프트 맥락으로만 쓰고 코드에서 분기하지 않는다.
    # Literal 로 좁히면 호출자가 새 카테고리를 추가할 때 422 로 깨지므로 str 로 둔다.
    category: str
    question: str
    # 이 질문이 원래 확인하려던 것. 재작성 후에도 이걸 그대로 확인할 수 있어야 한다.
    expected_answer: str
    based_on: list[str] = Field(default_factory=list)  # 근거 파일 경로. 프롬프트 맥락 용도.


class QuestionTailorRequest(BaseModel):
    interview_id: str
    user_id: str
    profile: CandidateProfile
    questions: tuple[OriginalQuestion, ...] = Field(..., min_length=1)
    callback_url: str

    # tuple 을 쓰는 이유는 JobRequest/FeedbackSoloRequest 와 동일 — 작업 내내 immutability 보장.


# ===== 산출물 =====


class TailoredQuestion(CamelModel):
    id: int  # 요청에 실려온 원질문 id 를 그대로 사용한다.
    question: str  # 재작성된 본문. 재작성 실패 시에는 원문이 그대로 들어간다.


class QuestionTailorResult(CamelModel):
    # False 면 재작성에 실패해 원질문을 그대로 돌려준 것이다.
    # 면접은 원질문으로도 열 수 있으므로 실패 콜백 대신 이 플래그로 알린다.
    tailored: bool
    questions: list[TailoredQuestion] = Field(..., min_length=1)


# ===== 콜백 페이로드 =====


class QuestionTailorErrorDetail(CamelModel):
    status_code: int  # 422(개인화 정보 없음), 500(내부 오류)
    message: str  # 사용자에게 노출 가능한 한글 메시지


class QuestionTailorCallbackSuccess(CamelModel):
    job_id: str
    # 면접 시작 전이라 세션이 아직 없다. 수신측은 interview_id 로 매칭한다.
    interview_id: str
    status: Literal["succeeded"] = "succeeded"
    result: QuestionTailorResult


class QuestionTailorCallbackFailure(CamelModel):
    job_id: str
    interview_id: str
    status: Literal["failed"] = "failed"
    error: QuestionTailorErrorDetail
