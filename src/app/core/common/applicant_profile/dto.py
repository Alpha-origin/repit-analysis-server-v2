from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.core.common.dto import CamelModel
from app.core.common.interview_qa.dto import ProjectSummary

# profile 형태가 바뀌면 올린다. /questions/cycle 은 아는 버전만 받는다(모르면 422).
SCHEMA_VERSION = 1


# ===== 작업 입력 (Command 진입점) =====


class ProfileJobRequest(BaseModel):
    # 전공. 자유 문자열이라 해석하지 않고 탐색 우선순위 지시에만 넣는다.
    major: str | None = None
    portfolio_url: str
    github_urls: tuple[str, ...] = Field(..., min_length=1)
    callback_url: str

    # tuple 을 쓰는 이유는 JobRequest 와 동일 — 작업 내내 immutability 보장.


# ===== 종합 데이터 (profile) =====
#
# LLM 의 submit_profile 입력(snake_case) 을 그대로 검증하고, 콜백에는 camelCase 로 나간다.
# /questions/cycle 이 API 서버에서 되돌려받을 때도 같은 모델로 읽는다.
# 목록 필드에 기본값을 두는 이유: 해당 재료가 없는 지원자도 흔하고, 비어 있다는 것 자체가
# "이 카테고리는 이용 불가"라는 정보다.


class Evidence(CamelModel):
    path: str  # 레포명/상대경로. 파일 또는 디렉터리.
    note: str = ""  # 이 경로에서 무엇을 확인했는지 한 줄.


class TechStackItem(CamelModel):
    name: str
    # 그 기술을 도입·설정한 파일(build.gradle, package.json, 설정 파일 등).
    evidence: list[Evidence] = Field(default_factory=list)


class RepositoryProfile(CamelModel):
    repo: str
    role: str
    description: str
    tech_stack: list[str] = Field(default_factory=list)


class CoreFeatureProfile(CamelModel):
    name: str
    description: str
    implementation: str = ""  # 코드에서 확인한 구현 방식.
    evidence: list[Evidence] = Field(default_factory=list)


class TroubleshootingProfile(CamelModel):
    title: str
    portfolio_claim: str = ""  # 포트폴리오가 서술한 문제와 해결.
    resolution_in_code: str = ""  # 그 해결이 코드에 어떻게 반영됐는지.
    evidence: list[Evidence] = Field(default_factory=list)


class IntegrationProfile(CamelModel):
    # from 은 파이썬 예약어라 필드명을 바꾸고 별칭으로 잇는다.
    from_: str = Field(..., alias="from")
    to: str
    method: str = ""
    evidence: list[Evidence] = Field(default_factory=list)


ClaimStatus = Literal["confirmed", "partial", "unverified"]


class ClaimCheck(CamelModel):
    # 근거 경로가 없다. 질문 생성 때 맥락으로만 쓰고 basedOn 으로는 쓰지 않는다.
    claim: str
    status: ClaimStatus
    note: str = ""


class StructureNote(CamelModel):
    repo: str
    summary: str
    key_paths: list[str] = Field(default_factory=list)  # 파일 또는 디렉터리 경로.


class ApplicantProfile(CamelModel):
    schema_version: int = SCHEMA_VERSION
    major: str | None = None
    overview: str = Field(..., min_length=1)
    tech_stack: list[TechStackItem] = Field(default_factory=list)
    repositories: list[RepositoryProfile] = Field(default_factory=list)
    core_features: list[CoreFeatureProfile] = Field(default_factory=list)
    troubleshootings: list[TroubleshootingProfile] = Field(default_factory=list)
    integrations: list[IntegrationProfile] = Field(default_factory=list)
    claim_checks: list[ClaimCheck] = Field(default_factory=list)
    structure_notes: list[StructureNote] = Field(default_factory=list)


# ===== 산출물 =====


class ProfileResult(CamelModel):
    profile: ApplicantProfile
    # tailor/multi 입력용. LLM 이 아니라 서버가 profile 에서 복사한다(summary.py).
    project_summary: ProjectSummary


# ===== 콜백 페이로드 =====
# 실패 콜백은 /generate 와 같은 형태라 interview_qa.dto.CallbackFailure 를 그대로 쓴다.


class ProfileCallbackSuccess(CamelModel):
    job_id: str
    status: Literal["succeeded"] = "succeeded"
    result: ProfileResult
