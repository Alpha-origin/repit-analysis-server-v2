from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.core.common.dto import CamelModel
from app.core.common.interview_qa.dto import ProjectSummary
from app.core.common.question_tailor.dto import OriginalQuestion

# 면접관 한 명이 맡는 원질문 수의 기본값. 요청에서 면접관마다 따로 지정할 수 있고,
# 지정하지 않으면 이 값이 쓰인다(기술 2 / 비개발 2 / 비개발 2 = 6문항).
DEFAULT_QUESTIONS_PER_PERSONA = 2

# 면접관 한 명에게 몰아줄 수 있는 문항 수 상한. 넘기면 면접 시간이 감당이 안 된다.
MAX_QUESTIONS_PER_PERSONA = 5

# 신규 생성 질문의 id 시작값. /generate 산출물이 항상 1~5 를 쓰므로 6 부터 시작하면
# API 서버가 그중 무엇을 골라 넘겼든 선택되지 않은 원질문과 id 가 부딪히지 않는다.
# (max(받은 id)+1 로 잡으면 1·2 를 넘겨받았을 때 3~6 이 되어 원질문 3·4·5 와 충돌한다.)
NEW_QUESTION_ID_START = 6


# ===== 작업 입력 (Command 진입점) =====


class TailorPersona(BaseModel):
    persona_id: str
    # 직책(기술/인사/CEO 등). Literal 로 좁히지 않는 이유는 OriginalQuestion.category 와 같다 —
    # 호출자가 새 직책을 추가할 때 422 로 깨지면 안 된다. 프롬프트에서만 해석한다.
    role: str
    # 말투. 어조에만 반영한다. 없으면 프롬프트에 넣지 않는다.
    style: str | None = None
    # 이 면접관이 맡는 문항 수. 기술 면접관은 받은 원질문 수와 같아야 하고,
    # 비개발 면접관은 이 수만큼 새로 생성된다. 라우터가 기본값을 채워 넘긴다.
    question_count: int = Field(..., ge=1, le=MAX_QUESTIONS_PER_PERSONA)


class MultiTailorRequest(BaseModel):
    interview_id: str
    user_id: str
    job_role: str | None = None  # 지원 직무. 리텍스팅의 개인화 축.
    experience_level: str | None = None  # 경력 수준. 리텍스팅의 개인화 축.

    # 기술 면접관 — 원질문을 리텍스팅해서 쓴다. 슬롯이 고정이라 요청에서 분리해 둔다.
    # role 문자열로 분기하면 호출자가 "TECH" 대신 "기술"을 보내는 순간 조용히 깨진다.
    tech_persona: TailorPersona
    # 비개발 면접관 — 질문을 새로 생성한다.
    other_personas: tuple[TailorPersona, ...] = Field(..., min_length=1)

    # 기술 면접관이 쓸 원질문. 어떤 것을 쓸지는 API 서버가 골라서 넘긴다.
    questions: tuple[OriginalQuestion, ...] = Field(..., min_length=1)
    # 신규 질문 생성의 근거. /generate 산출물을 그대로 되돌려받는다.
    project_summary: ProjectSummary
    callback_url: str

    # tuple 을 쓰는 이유는 QuestionTailorRequest 와 동일 — 작업 내내 immutability 보장.


# ===== 단계 간 자료형 =====


class GeneratedQuestion(BaseModel):
    """1단계(생성) 산출물. id 는 아직 붙지 않았다 — 채번은 서버 몫이다."""

    persona_id: str
    category: str
    question: str
    expected_answer: str
    based_on: list[str]


# ===== 산출물 =====


class MultiTailoredQuestion(CamelModel):
    # /generate 산출물(InterviewItem) 과 같은 형태 + personaId.
    # 소켓 서버는 이 순서를 그대로 questions 에 넣으면 진행 순서가 완성된다.
    id: int
    persona_id: str
    category: str
    question: str
    expected_answer: str
    based_on: list[str]


class MultiTailorResult(CamelModel):
    # solo 의 tailored 플래그가 없다. 폴백 없이 전부 실패 처리하므로
    # 성공 콜백이 왔다면 질문은 항상 전부 채워져 있다.
    questions: list[MultiTailoredQuestion] = Field(..., min_length=1)


# ===== 콜백 페이로드 =====


class MultiTailorErrorDetail(CamelModel):
    status_code: int  # 422(입력 부족), 502(LLM 호출 실패), 500(내부 오류)
    message: str  # 사용자에게 노출 가능한 한글 메시지


class MultiTailorCallbackSuccess(CamelModel):
    job_id: str
    # 면접 시작 전이라 세션이 아직 없다. 수신측은 interview_id 로 매칭한다.
    interview_id: str
    status: Literal["succeeded"] = "succeeded"
    result: MultiTailorResult


class MultiTailorCallbackFailure(CamelModel):
    job_id: str
    interview_id: str
    status: Literal["failed"] = "failed"
    error: MultiTailorErrorDetail
