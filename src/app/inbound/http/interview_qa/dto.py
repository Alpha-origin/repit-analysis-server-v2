"""POST /generate 의 HTTP 요청/즉시 응답 DTO.

- ``GenerateRequest``: 클라이언트가 보내는 요청 본문. ``HttpUrl`` 로 형식 검증.
- ``JobAccepted``: 작업 접수 즉시 응답(``202``).

파이프라인 산출물·콜백 페이로드는 도메인 모델(``core/common/interview_qa/dto.py``) 에
정의하고, 엔드포인트에서 평문 ``str`` 로 변환해 Command 로 넘긴다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class GenerateRequest(BaseModel):
    """POST /generate 요청 본문."""

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


class JobAccepted(BaseModel):
    """POST /generate 즉시 응답 — 작업 접수 확인."""

    job_id: str = Field(..., description="이번 작업의 식별자(UUIDv4). 콜백 페이로드와 매칭에 사용.")
    status: Literal["accepted"] = "accepted"
    message: str = "면접 Q&A 생성 작업을 시작했습니다. 완료 시 callback_url 로 결과를 전송합니다."
