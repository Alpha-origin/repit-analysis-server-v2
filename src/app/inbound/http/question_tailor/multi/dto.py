from __future__ import annotations

from typing import Literal

from pydantic import Field, HttpUrl, model_validator

from app.core.common.dto import CamelModel
from app.core.common.interview_qa.dto import RepoRole
from app.core.common.question_tailor.multi.dto import (
    DEFAULT_QUESTIONS_PER_PERSONA,
    MAX_QUESTIONS_PER_PERSONA,
)

# 기술 면접관에게 넘길 원질문 수 상한. /generate 산출물은 5문항이고 그중 일부만 골라 오지만
# 상한만 여유를 둔다.
MAX_QUESTIONS = 10

# 비개발 면접관 수 상한. 늘어날수록 생성 문항과 면접 시간이 선형으로 늘어난다.
MAX_OTHER_PERSONAS = 4


class TailorPersonaRequest(CamelModel):
    persona_id: str = Field(..., min_length=1, description="페르소나 식별자. 질문마다 그대로 되돌려준다.")
    role: str = Field(..., min_length=1, description="직책(TECH/HR/CEO 등). 질문 관점과 채점 관점을 결정한다.")
    style: str | None = Field(default=None, description="말투. 질문 어조에만 반영된다.")
    question_count: int = Field(
        default=DEFAULT_QUESTIONS_PER_PERSONA,
        ge=1,
        le=MAX_QUESTIONS_PER_PERSONA,
        description=(
            "이 면접관이 맡을 문항 수. 생략하면 2. "
            "기술 면접관은 questions 배열 길이와 같아야 한다(넘겨준 원질문을 그대로 맡기 때문)."
        ),
    )


class RepositorySummaryRequest(CamelModel):
    repo: str = Field(..., description="저장소 이름")
    role: RepoRole = Field(..., description="저장소 역할(api_server/frontend 등)")
    description: str = Field(..., description="저장소 설명")


class CoreFeatureRequest(CamelModel):
    name: str = Field(..., description="기능 이름. 비개발 질문의 주 재료가 된다.")
    description: str = Field(..., description="기능 설명")
    based_on: list[str] = Field(..., min_length=1, description="근거 파일 경로")


class ProjectSummaryRequest(CamelModel):
    # /generate 산출물의 projectSummary 를 그대로 되돌려받는 형태.
    overview: str = Field(..., min_length=1, description="프로젝트 개요")
    repositories: list[RepositorySummaryRequest] = Field(default_factory=list, description="저장소 목록")
    core_features: list[CoreFeatureRequest] = Field(default_factory=list, description="핵심 기능 목록")
    tech_stack: list[str] = Field(default_factory=list, description="기술 스택")


class MultiOriginalQuestionRequest(CamelModel):
    id: int = Field(..., ge=1, description="원질문 식별자. 재작성 결과와 매칭하는 키.")
    category: str = Field(..., min_length=1, description="질문 카테고리(tech_choice 등)")
    question: str = Field(..., min_length=1, description="원질문 본문. 재작성 대상.")
    expected_answer: str = Field(..., min_length=1, description="원질문이 확인하려던 것")
    based_on: list[str] = Field(default_factory=list, description="근거 파일 경로")


class MultiTailorHttpRequest(CamelModel):
    interview_id: str = Field(..., description="면접 식별자. 면접 시작 전이라 세션은 아직 없다.")
    user_id: str = Field(..., description="사용자 식별자")
    job_role: str | None = Field(default=None, description="지원 직무. 기술 질문 재작성의 개인화 축.")
    experience_level: str | None = Field(default=None, description="경력 수준. 기술 질문 재작성의 개인화 축.")

    tech_persona: TailorPersonaRequest = Field(..., description="기술 면접관. 원질문을 다시 써서 쓴다.")
    other_personas: list[TailorPersonaRequest] = Field(
        ...,
        min_length=1,
        max_length=MAX_OTHER_PERSONAS,
        description="비개발 면접관 목록. 각자 몫의 질문이 새로 생성된다.",
    )
    questions: list[MultiOriginalQuestionRequest] = Field(
        ...,
        min_length=1,
        max_length=MAX_QUESTIONS,
        description="기술 면접관이 쓸 원질문. 무엇을 쓸지는 API 서버가 고른다.",
    )
    project_summary: ProjectSummaryRequest = Field(..., description="신규 질문 생성의 근거")
    callback_url: HttpUrl = Field(..., description="완료/실패 시 결과를 POST 로 받을 URL")

    @model_validator(mode="after")
    def _check_unique_ids(self) -> MultiTailorHttpRequest:
        # id 로 재작성 결과를 매칭하므로 중복이 있으면 한쪽이 조용히 덮인다.
        ids = [question.id for question in self.questions]
        if len(set(ids)) != len(ids):
            raise ValueError("questions 의 id 는 서로 달라야 합니다.")
        return self

    @model_validator(mode="after")
    def _check_tech_question_count(self) -> MultiTailorHttpRequest:
        # 기술 면접관은 질문을 새로 만들지 않고 넘겨받은 원질문을 그대로 맡는다.
        # 개수가 어긋나면 배분이 설계와 달라지므로 여기서 막는다(422).
        if len(self.questions) != self.tech_persona.question_count:
            raise ValueError(
                f"techPersona.questionCount({self.tech_persona.question_count}) 와 "
                f"questions 개수({len(self.questions)}) 가 같아야 합니다."
            )
        return self

    @model_validator(mode="after")
    def _check_unique_personas(self) -> MultiTailorHttpRequest:
        persona_ids = [self.tech_persona.persona_id, *(persona.persona_id for persona in self.other_personas)]
        if len(set(persona_ids)) != len(persona_ids):
            raise ValueError("personaId 는 면접관마다 달라야 합니다.")
        # 같은 직책 둘을 넣으면 질문 축이 겹쳐 면접관별 피드백이 사실상 같은 말이 된다.
        roles = [persona.role.strip().lower() for persona in self.other_personas]
        if len(set(roles)) != len(roles):
            raise ValueError("otherPersonas 의 role 은 서로 달라야 합니다.")
        return self


class MultiTailorJobAccepted(CamelModel):
    job_id: str = Field(..., description="이번 작업의 식별자(UUIDv4). 콜백 페이로드와 매칭에 사용.")
    interview_id: str = Field(..., description="요청에 실려온 면접 식별자를 그대로 되돌려준다.")
    status: Literal["accepted"] = "accepted"
    message: str = "N:1 질문 구성 작업을 시작했습니다. 완료 시 callbackUrl 로 결과를 전송합니다."
