from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, HttpUrl, model_validator

from app.core.common.dto import CamelModel

# 소켓 서버(Java) 의 QuestionType enum 과 같은 값.
QuestionType = Literal["ORIGINAL", "FOLLOW"]

# 한 세션에서 받을 질문 수 상한. 채점은 1회 LLM 호출로 처리하므로 프롬프트 비대화를 막는다.
MAX_QUESTIONS = 50

# 면접관 수 상한. N:1 은 3인 구성이지만 확장 여지를 둔다.
MAX_PERSONAS = 6


class FeedbackPersonaRequest(CamelModel):
    persona_id: str = Field(..., min_length=1, description="페르소나 식별자")
    role: str = Field(..., min_length=1, description="직책(TECH/HR/CEO 등). 채점 관점을 결정한다.")
    style: str | None = Field(default=None, description="말투. 피드백 어조에만 반영된다.")


class MultiInterviewQuestionRequest(CamelModel):
    question_id: str = Field(..., description="질문 식별자. 피드백 결과와 매칭하는 키.")
    persona_id: str = Field(..., min_length=1, description="이 질문을 한 면접관. 꼬리질문은 부모의 값을 상속한다.")
    parent_id: str | None = Field(default=None, description="FOLLOW 질문의 부모 질문 id. ORIGINAL 이면 null.")
    type: QuestionType = Field(..., description="ORIGINAL(최초 질문) 또는 FOLLOW(꼬리 질문)")
    intention: str = Field(..., min_length=1, description="질문 의도. 채점의 유일한 기준이 된다.")
    content: str = Field(..., min_length=1, description="질문 본문")
    created_at: datetime = Field(..., description="질문 생성 시각")

    @model_validator(mode="after")
    def _check_parent_id(self) -> MultiInterviewQuestionRequest:
        # 형식 단계에서 잡을 수 있는 모순. "부모가 실제로 존재하는가"는 조립 단계의 관심사다.
        if self.type == "FOLLOW" and self.parent_id is None:
            raise ValueError("FOLLOW 질문에는 parentId 가 필요합니다.")
        if self.type == "ORIGINAL" and self.parent_id is not None:
            raise ValueError("ORIGINAL 질문에는 parentId 가 없어야 합니다.")
        return self


class MultiInterviewAnswerRequest(CamelModel):
    answer_id: str = Field(..., description="답변 식별자")
    question_id: str = Field(..., description="이 답변이 속한 질문 id")
    # 빈 문자열/공백은 미답변으로 처리하므로 길이 제약을 걸지 않는다.
    content: str = Field(..., description="사용자 답변 본문")
    created_at: datetime = Field(..., description="답변 생성 시각")


class FeedbackMultiHttpRequest(CamelModel):
    session_id: str = Field(..., description="면접 세션 식별자. N:1 도 세션은 하나다.")
    interview_id: str = Field(..., description="면접 식별자")
    user_id: str = Field(..., description="사용자 식별자")
    personas: list[FeedbackPersonaRequest] = Field(
        ...,
        min_length=1,
        max_length=MAX_PERSONAS,
        description="면접에 참여한 면접관 전원. 담당 문항이 없어도 결과에는 포함된다.",
    )
    questions: list[MultiInterviewQuestionRequest] = Field(
        ...,
        min_length=1,
        max_length=MAX_QUESTIONS,
        description="면접에서 제시된 질문 목록(ORIGINAL + FOLLOW). 진행 순서대로 넣는다.",
    )
    answers: list[MultiInterviewAnswerRequest] = Field(
        ...,
        min_length=1,
        description="사용자 답변 목록. 질문 수와 일치하지 않아도 된다(미답변 허용).",
    )
    callback_url: HttpUrl = Field(..., description="채점 완료/실패 시 결과를 POST 로 받을 URL")

    @model_validator(mode="after")
    def _check_personas(self) -> FeedbackMultiHttpRequest:
        persona_ids = [persona.persona_id for persona in self.personas]
        if len(set(persona_ids)) != len(persona_ids):
            raise ValueError("personaId 는 면접관마다 달라야 합니다.")

        # 질문이 명단에 없는 면접관을 가리키면 그 문항의 채점 관점이 사라진다.
        # 조립까지 가서 조용히 빈 값이 되는 것보다 여기서 422 로 막는 편이 낫다.
        known = set(persona_ids)
        unknown = sorted({question.persona_id for question in self.questions} - known)
        if unknown:
            raise ValueError(f"personas 에 없는 personaId 를 가진 질문이 있습니다: {', '.join(unknown)}")
        return self


class MultiFeedbackJobAccepted(CamelModel):
    job_id: str = Field(..., description="이번 작업의 식별자(UUIDv4). 콜백 페이로드와 매칭에 사용.")
    session_id: str = Field(..., description="요청에 실려온 세션 식별자를 그대로 되돌려준다.")
    status: Literal["accepted"] = "accepted"
    message: str = "N:1 답변 피드백 생성 작업을 시작했습니다. 완료 시 callbackUrl 로 결과를 전송합니다."
