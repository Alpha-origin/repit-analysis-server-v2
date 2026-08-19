from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.core.common.feedback.dto import CamelModel

# 소켓 서버(Java) 의 QuestionType enum 과 같은 값. Jackson 이 enum 이름을 그대로 직렬화한다.
# ORIGINAL: 최초 질문, FOLLOW: 꼬리 질문(parent_id 로 부모를 가리킨다).
QuestionType = Literal["ORIGINAL", "FOLLOW"]


# ===== 작업 입력 (Command 진입점) =====


class FeedbackQuestion(BaseModel):
    question_id: str
    parent_id: str | None = None  # FOLLOW 일 때만 채워진다.
    type: QuestionType
    intention: str  # 채점의 유일한 기준. 이 파이프라인에는 모범답안이 존재하지 않는다.
    content: str
    created_at: datetime


class FeedbackAnswer(BaseModel):
    answer_id: str
    question_id: str
    content: str  # 빈 문자열/공백이면 조립 단계에서 미답변으로 처리된다.
    created_at: datetime


class FeedbackSoloRequest(BaseModel):
    session_id: str
    interview_id: str
    user_id: str
    persona_type: str | None = None  # 피드백 어조에 반영된다.
    questions: tuple[FeedbackQuestion, ...] = Field(..., min_length=1)
    answers: tuple[FeedbackAnswer, ...] = Field(..., min_length=1)
    callback_url: str

    # tuple 을 쓰는 이유는 JobRequest 와 동일 — 작업 내내 immutability 보장.


# ===== 조립 결과 =====


class GradingTarget(BaseModel):
    question_id: str
    type: QuestionType
    intention: str
    content: str
    answer: str
    # FOLLOW 채점용 부모 맥락. 부모를 못 찾거나 부모에 답변이 없으면 None 으로 남는다.
    parent_question: str | None = None
    parent_answer: str | None = None


class AssembledSession(BaseModel):
    targets: tuple[GradingTarget, ...]  # 답변이 있는 문항만. 채점 대상.
    unanswered_question_ids: tuple[str, ...]  # 채점하지 않지만 총평 집계에는 반영된다.
    question_count: int  # 미답변 포함 전체 질문 수.


# ===== 산출물 =====


class FrequentWord(CamelModel):
    word: str
    count: int


class AnswerFeedback(CamelModel):
    question_id: str
    # 아래 3개는 요청 body 를 그대로 되돌려주는 값. LLM 이 생성하지 않는다.
    # (3000자짜리 답변을 LLM 에게 복창시키면 토큰이 폭증하고 원문이 변형될 위험도 있다.)
    question_content: str
    intention: str
    user_answer: str
    # 채점 기준이 아니라 사용자에게 보여주는 예시 답안. 40~100자.
    model_answer: str
    strengths: list[str]
    improvements: list[str]
    comment: str  # 한 줄 총평.


class OverallFeedback(CamelModel):
    # 3지표는 서로 다른 축이다. 프롬프트에서 축을 구분하지 않으면 같은 값으로 뭉친다.
    total_score: int = Field(..., ge=0, le=100)  # 면접 전체 종합 평가
    intent_alignment_score: int = Field(..., ge=0, le=100)  # 물은 것에 답했는가
    reliability_score: int = Field(..., ge=0, le=100)  # 일관성(모순·근거 구체성)
    summary: str
    strengths: list[str]
    improvements: list[str]
    # 아래 3개는 서버가 채운다. LLM 도구 스키마에는 없다.
    frequent_words: list[FrequentWord]
    answered_count: int
    question_count: int


class InterviewFeedbackResult(CamelModel):
    overall: OverallFeedback
    feedbacks: list[AnswerFeedback] = Field(..., min_length=1)


class FeedbackCallbackSuccess(CamelModel):
    job_id: str
    session_id: str
    status: Literal["succeeded"] = "succeeded"
    result: InterviewFeedbackResult
