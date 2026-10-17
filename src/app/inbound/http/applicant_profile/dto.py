from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, HttpUrl, model_validator

from app.core.common.dto import CamelModel
from app.core.common.interview_qa.stage2_nondev.dto import ProfileSupplement
from app.core.common.job_spec import find_job


class ProfileDocumentRequest(CamelModel):
    type: str
    url: HttpUrl
    title: str | None = None


class ProfileSupplementRequest(ProfileSupplement):
    title: str = Field(min_length=1, max_length=80)
    document_index: int | None = Field(default=None, ge=0)
    goal: str = Field(default="", max_length=500)
    role: str = Field(default="", max_length=500)
    decision: str = Field(default="", max_length=500)
    outcome: str = Field(default="", max_length=500)


class ProfileRequest(CamelModel):
    @model_validator(mode="before")
    @classmethod
    def ignore_legacy_when_job_role_present(cls, value: Any) -> Any:
        if isinstance(value, dict) and value.get("jobRole", value.get("job_role")) is not None:
            value = value.copy()
            for key in ("major", "portfolioUrl", "portfolio_url"):
                value.pop(key, None)
        return value

    major: str | None = Field(default=None, description="전공. 자유 문자열. 탐색 우선순위에만 반영한다.")
    portfolio_url: HttpUrl | None = Field(default=None, description="옛 요청의 포트폴리오 PDF URL")
    github_urls: list[HttpUrl] = Field(
        default_factory=list,
        description="GitHub public 저장소 URL 목록 (1개 이상)",
    )
    callback_url: HttpUrl = Field(..., description="작업 완료/실패 시 결과를 POST 로 받을 URL")
    job_role: str | None = None
    documents: list[ProfileDocumentRequest] = Field(default_factory=list, max_length=5)
    supplements: list[ProfileSupplementRequest] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def validate_materials(self) -> ProfileRequest:
        if self.job_role is None:
            if self.portfolio_url is None or not self.github_urls:
                raise ValueError("옛 요청에는 portfolioUrl과 githubUrls 1개 이상이 필요합니다.")
            return self
        job = find_job(self.job_role)
        if job is None:
            raise ValueError("지원하지 않는 jobRole입니다.")
        self.job_role = job.code
        types = {document.type for document in self.documents}
        if not self.documents or not types <= job.allowed_docs or not types & job.required_docs:
            raise ValueError("직무에 허용된 문서 1~5개와 필수 종류 1개 이상이 필요합니다.")
        if job.github == "REQUIRED" and not self.github_urls:
            raise ValueError("개발 직무에는 GitHub 저장소가 필요합니다.")
        if job.github == "HIDDEN" and self.github_urls:
            raise ValueError("해당 직무에는 GitHub 입력을 받지 않습니다.")
        if self.supplements and not job.supplements:
            raise ValueError("해당 직무에는 보완 입력을 받지 않습니다.")
        if any(
            supplement.document_index is not None and supplement.document_index >= len(self.documents)
            for supplement in self.supplements
        ):
            raise ValueError("보완 입력 documentIndex가 문서 범위를 벗어났습니다.")
        return self


class ProfileJobAccepted(CamelModel):
    job_id: str = Field(..., description="이번 작업의 식별자(UUIDv4). 콜백 페이로드와 매칭에 사용.")
    status: Literal["accepted"] = "accepted"
    message: str = "종합 데이터 생성 작업을 시작했습니다. 완료 시 callbackUrl 로 결과를 전송합니다."
