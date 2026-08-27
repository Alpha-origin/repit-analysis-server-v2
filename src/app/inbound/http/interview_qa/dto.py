
from __future__ import annotations

from typing import Literal

from pydantic import Field, HttpUrl

from app.core.common.dto import CamelModel


class GenerateRequest(CamelModel):
    # camelCase 로 통일했지만 populate_by_name 덕에 기존 snake_case 요청도 그대로 받는다.
    # 호출자를 한 번에 바꾸지 않아도 되도록 남겨 둔 여지다.

    portfolio_url: HttpUrl = Field(..., description="포트폴리오 PDF 다운로드 URL (필수, 1개)")
    github_urls: list[HttpUrl] = Field(
        ...,
        min_length=1,
        description="GitHub public 저장소 URL 목록 (1개 이상)",
    )
    callback_url: HttpUrl = Field(
        ...,
        description="작업 완료/실패 시 결과를 POST 로 받을 URL",
    )


class JobAccepted(CamelModel):

    job_id: str = Field(..., description="이번 작업의 식별자(UUIDv4). 콜백 페이로드와 매칭에 사용.")
    status: Literal["accepted"] = "accepted"
    message: str = "면접 Q&A 생성 작업을 시작했습니다. 완료 시 callbackUrl 로 결과를 전송합니다."
