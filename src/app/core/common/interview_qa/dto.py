
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.core.common.dto import CamelModel

# ===== 작업 입력 (Command 진입점) =====


class JobRequest(BaseModel):

    portfolio_url: str
    github_urls: tuple[str, ...] = Field(..., min_length=1)
    callback_url: str

    # tuple 을 쓰는 이유: 작업 내내 immutability 보장(실수로 수정될 여지 없음).


# ===== 단계 간 자료형 =====


class RepoMeta(BaseModel):
    owner: str  # 저장소 소유자(개인 또는 organization).
    name: str  # 저장소 이름.
    default_branch: str  # 기본 브랜치(main / master 등).
    is_private: bool  # private 저장소면 True. Stage 1 에서 차단 대상.


class Stage1Result(BaseModel):
    # bytes 직렬화는 사용하지 않음 — 메모리 안에서만 다음 단계로 전달된다.
    pdf_bytes: bytes
    # tuple 으로 immutability 확보. 길이 제한은 입력측에서 이미 보장.
    repos: tuple[RepoMeta, ...]


# 좌표는 (x0, y0, x1, y1) 형태로 통일. y 가 위→아래로 증가하는 fitz 좌표계 그대로.
BBox = tuple[float, float, float, float]


class TextBlock(BaseModel):
    text: str  # 노이즈 제거 후의 정제된 텍스트(빈 문자열이면 블록 자체가 제거된 셈).
    bbox: BBox  # 페이지 내 좌표(x0, y0, x1, y1).


class ImageBlock(BaseModel):
    image_bytes: bytes  # 원본 이미지 바이트(png/jpg 등 fitz 가 추출한 그대로).
    width: int  # 픽셀 단위 가로.
    height: int  # 픽셀 단위 세로.
    bbox: BBox  # 페이지 내 좌표.
    xref: int  # fitz 의 이미지 참조 번호. 같은 이미지가 여러 페이지에 반복될 때 동일.


class PdfPage(BaseModel):

    page_number: int  # 0부터 시작.
    page_width: float  # 페이지 가로(포인트 단위).
    page_height: float  # 페이지 세로.
    text_blocks: list[TextBlock]
    image_blocks: list[ImageBlock]


class ParsedPortfolio(BaseModel):

    pages: list[PdfPage]


# 이미지 비중에 따른 처리 분기.
# - ``text_heavy``: 정보성 이미지가 임계 미만. 이미지 구조화 단계를 건너뛴다.
# - ``image_heavy``: 정보성 이미지가 임계 이상. 2차 LLM 트리아지 + 구조화 진행.
PdfBranch = Literal["text_heavy", "image_heavy"]


class TriagedPortfolio(BaseModel):

    pages: list[PdfPage]
    branch: PdfBranch
    info_img_count: int


# 비전 구조화에서 분류할 이미지 유형. tool-use 스키마 enum 과 동일.
ImageType = Literal[
    "architecture_diagram",
    "erd",
    "ui_screenshot",
    "flow_diagram",
    "chart",
    "other",
]


class ImageStructure(BaseModel):
    source_page: int  # 0부터 시작하는 페이지 번호. 본문 삽입 위치 결정에 사용.
    image_type: str  # 유형(보통 ``ImageType`` 중 하나, 모델이 알 수 없는 값을 주면 그대로 보관).
    summary: str  # 한두 문장 한국어 요약.
    tech_signals: list[str]  # 이미지에서 추출된 기술 키워드.


class StructuredPortfolio(BaseModel):
    pages: list[PdfPage]
    structured_images: list[ImageStructure]
    branch: PdfBranch


class MergedDocument(BaseModel):
    portfolio_text: str
    branch: PdfBranch


# 저장소 역할 라벨. unknown 은 판정 실패(LLM 이 코드 보고 직접 판단).
RepoRole = Literal["api_server", "ai_server", "frontend", "infra", "unknown"]


class RepoTree(BaseModel):
    name: str  # 논리적 저장소 이름(RepoMeta.name 동일).
    role: RepoRole
    # 저장소 루트 기준 상대 경로 목록(블랙리스트 통과분), 정렬됨.
    file_paths: list[str]


class Stage3Result(BaseModel):
    repos: list[RepoTree]
    tree_text: str
    path_index: dict[str, str]


# ===== 파이프라인 산출물 =====


# 면접 질문 카테고리 — 5종 enum.
# tech_choice: 기술 선택 근거, implementation: 구현 방식 검증,
# troubleshooting: 트러블슈팅 심층, integration: 레포 간 연동,
# structure: 다른 기능/전체 구조.
QuestionCategory = Literal[
    "tech_choice",
    "implementation",
    "troubleshooting",
    "integration",
    "structure",
]


class RepositorySummary(CamelModel):
    repo: str  # 저장소 이름 (예: ``order-api``)
    role: str  # 역할 라벨 (api_server / ai_server / frontend / infra / unknown)
    description: str  # 코드·포트폴리오 근거로 채운 한두 줄 설명


class CoreFeature(CamelModel):
    name: str  # 기능 이름
    description: str  # 기능 설명
    based_on: list[str] = Field(default_factory=list)  # 근거 파일 경로(레포명/상대경로)


class ProjectSummary(CamelModel):
    overview: str
    repositories: list[RepositorySummary]
    core_features: list[CoreFeature]
    tech_stack: list[str]


class InterviewItem(CamelModel):
    id: int = Field(..., ge=1, le=5)  # 1~5 고정
    category: QuestionCategory
    question: str
    expected_answer: str
    # 근거 파일 경로(또는 ``["file_tree"]``) — 최소 1개 필수.
    # 추측 질문 방지 장치이므로 절대 비울 수 없다.
    based_on: list[str] = Field(..., min_length=1)


class InterviewQaResult(CamelModel):
    project_summary: ProjectSummary
    interview: list[InterviewItem] = Field(..., min_length=5, max_length=5)


# ===== 콜백 페이로드 =====
# 여기부터 밖으로 나가는 것은 전부 camelCase 다(jobId / statusCode).
# 내부 단계 자료형이 snake_case 인 것과 무관하게, 수신측이 보는 표기는 하나로 맞춘다.


class CallbackSuccess(CamelModel):
    job_id: str
    status: Literal["succeeded"] = "succeeded"
    result: InterviewQaResult


class CallbackErrorDetail(CamelModel):
    status_code: int  # 422(잘못된 PDF), 403(private repo), 500(내부 오류) 등
    message: str  # 사용자에게 노출 가능한 한글 메시지


class CallbackFailure(CamelModel):
    job_id: str
    status: Literal["failed"] = "failed"
    error: CallbackErrorDetail
