from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.core.common.applicant_profile.dto import ApplicantProfile
from app.core.common.dto import CamelModel
from app.core.common.interview_qa.dto import QuestionCategory

# SOLO 는 1:1 면접(세트당 5문항), MULTI 는 N:1 면접의 기술 면접관 몫(세트당 2문항)이다.
CycleMode = Literal["SOLO", "MULTI"]


# ===== 작업 입력 (Command 진입점) =====


class QuestionCycleRequest(BaseModel):
    mode: CycleMode
    # /profile 결과를 API 서버가 저장했다가 그대로 되돌려준 것.
    profile: ApplicantProfile
    # 같은 모드의 최근 2사이클 질문 본문. 같은 내용을 다시 묻지 않게 프롬프트에 싣는다.
    exclude_questions: tuple[str, ...] = ()
    callback_url: str

    # tuple 을 쓰는 이유는 JobRequest 와 동일 — 작업 내내 immutability 보장.


# ===== 산출물 =====


class CycleQuestion(CamelModel):
    set_no: int = Field(..., ge=1)  # 1~3. 면접 한 번에 세트 하나를 꺼내 쓴다.
    category: QuestionCategory
    question: str
    # 이 질문으로 확인하려는 것 한 문장. 채점 기준이 된다.
    intention: str
    # 모범답안. 꼬리질문 생성과 참고용이고 채점 기준은 아니다.
    expected_answer: str
    # profile 의 근거 경로 집합 안의 경로. 최소 1개.
    based_on: list[str] = Field(..., min_length=1)


class QuestionCycleResult(CamelModel):
    mode: CycleMode
    # setNo, 카테고리 순으로 정렬돼 있다.
    questions: list[CycleQuestion] = Field(..., min_length=1)


# ===== 콜백 페이로드 =====
# 실패 콜백은 /generate 와 같은 형태라 interview_qa.dto.CallbackFailure 를 그대로 쓴다.


class QuestionCycleCallbackSuccess(CamelModel):
    job_id: str
    status: Literal["succeeded"] = "succeeded"
    result: QuestionCycleResult
