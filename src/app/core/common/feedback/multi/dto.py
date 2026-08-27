from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.core.common.dto import CamelModel
from app.core.common.feedback.solo.dto import (
    AnswerFeedback,
    FeedbackAnswer,
    FeedbackQuestion,
    OverallFeedback,
)

# ===== 작업 입력 (Command 진입점) =====


class FeedbackPersona(BaseModel):
    persona_id: str
    # 직책(기술/인사/CEO 등). 1:1 과 달리 채점 관점에 반영된다 —
    # CEO 질문을 기술 깊이로 감점하면 직책을 나눈 의미가 사라진다.
    role: str
    # 말투. 1:1 과 동일하게 어조에만 반영한다. 점수에 영향을 주지 않는다.
    style: str | None = None


class MultiFeedbackQuestion(FeedbackQuestion):
    # 1:1 질문과 같은 구조에 "누가 물었는지"만 더한다. 체인(parent_id) 구조는 동일하다.
    persona_id: str


class FeedbackMultiRequest(BaseModel):
    session_id: str
    interview_id: str
    user_id: str
    # 면접에 참여한 면접관 전원. 질문이 하나도 없는 면접관도 결과에는 나와야 하므로 따로 받는다.
    personas: tuple[FeedbackPersona, ...] = Field(..., min_length=1)
    questions: tuple[MultiFeedbackQuestion, ...] = Field(..., min_length=1)
    answers: tuple[FeedbackAnswer, ...] = Field(..., min_length=1)
    callback_url: str

    # tuple 을 쓰는 이유는 FeedbackSoloRequest 와 동일 — 작업 내내 immutability 보장.


# ===== 산출물 =====


class PersonaFeedback(CamelModel):
    persona_id: str
    role: str
    # 면접관별로는 점수를 하나만 둔다. 담당 문항이 2~3개뿐이라
    # 그 안에서 "답변끼리 모순이 없는가"(신뢰성) 를 판단하는 것은 의미가 없다.
    # 3지표는 overall 에만 둔다.
    score: int = Field(..., ge=0, le=100)
    comment: str  # 이 면접관 시점의 한 줄 총평.
    strengths: list[str]
    improvements: list[str]
    # 이 면접관이 맡은 문항 기준 집계. LLM 산출물이 아니라 서버가 센다.
    # overall 의 같은 이름 필드는 면접 전체 기준이라 값이 다르다.
    answered_count: int  # 담당 문항 중 답변이 있었던 수
    question_count: int  # 담당 문항 수(미답변 포함)


class MultiAnswerFeedback(AnswerFeedback):
    # 1:1 문항 피드백 + 어느 면접관의 질문이었는지.
    persona_id: str


class MultiInterviewFeedbackResult(CamelModel):
    # 1:1 의 2계층(overall + feedbacks) 에 면접관 레이어가 하나 더 얹힌다.
    overall: OverallFeedback
    personas: list[PersonaFeedback] = Field(..., min_length=1)
    feedbacks: list[MultiAnswerFeedback] = Field(..., min_length=1)


class MultiFeedbackCallbackSuccess(CamelModel):
    job_id: str
    session_id: str
    status: Literal["succeeded"] = "succeeded"
    result: MultiInterviewFeedbackResult
