"""파이프라인 도메인 DTO.

FastAPI/HTTP 와 무관한 순수 데이터 모델. Command·단계 서비스가 주고받는
값들을 정의한다. 외부 표현(``HttpUrl`` 등) 은 inbound 라우터에서 평문 ``str`` 로
변환한 뒤 이 모델로 전달된다.

콜백 페이로드도 여기에 둔다 — outbound 어댑터가 직렬화해 클라이언트로 POST 한다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ===== 작업 입력 (Command 진입점) =====


class JobRequest(BaseModel):
    """백그라운드 작업이 받는 입력 — 모두 평문 ``str``."""

    portfolio_url: str
    github_urls: tuple[str, ...] = Field(..., min_length=1)
    callback_url: str

    # tuple 을 쓰는 이유: 작업 내내 immutability 보장(실수로 수정될 여지 없음).


# ===== 단계 간 자료형 =====


class RepoMeta(BaseModel):
    """GitHub 저장소 메타데이터.

    이후 단계(파일 트리 생성) 에서 default_branch 로 tarball 을 받기 위해 보관.
    """

    owner: str  # 저장소 소유자(개인 또는 organization).
    name: str  # 저장소 이름.
    default_branch: str  # 기본 브랜치(main / master 등).
    is_private: bool  # private 저장소면 True. Stage 1 에서 차단 대상.


class Stage1Result(BaseModel):
    """입력 검증 통과 시 다음 단계로 전달되는 자료.

    - ``pdf_bytes``: 검증된 PDF 의 원본 바이트(이후 fitz 로 다시 열어 사용).
    - ``repos``: 모두 public 으로 확인된 저장소 메타.
    """

    # bytes 직렬화는 사용하지 않음 — 메모리 안에서만 다음 단계로 전달된다.
    pdf_bytes: bytes
    # tuple 으로 immutability 확보. 길이 제한은 입력측에서 이미 보장.
    repos: tuple[RepoMeta, ...]


# 좌표는 (x0, y0, x1, y1) 형태로 통일. y 가 위→아래로 증가하는 fitz 좌표계 그대로.
BBox = tuple[float, float, float, float]


class TextBlock(BaseModel):
    """페이지 안의 텍스트 블록 한 덩어리.

    fitz 가 묶어 준 단위 그대로 보관한다(보통 한 문단). 좌표를 같이 가지고 다녀야
    다음 단계의 캡션/제목 추정에 활용할 수 있다.
    """

    text: str  # 노이즈 제거 후의 정제된 텍스트(빈 문자열이면 블록 자체가 제거된 셈).
    bbox: BBox  # 페이지 내 좌표(x0, y0, x1, y1).


class ImageBlock(BaseModel):
    """페이지 안의 이미지 한 개.

    이후 트리아지·구조화 단계에서 좌표(주변 텍스트로 캡션 찾기)와 픽셀 크기
    (장식 여부 판정)를 모두 사용하므로 두 정보 모두 남긴다.
    """

    image_bytes: bytes  # 원본 이미지 바이트(png/jpg 등 fitz 가 추출한 그대로).
    width: int  # 픽셀 단위 가로.
    height: int  # 픽셀 단위 세로.
    bbox: BBox  # 페이지 내 좌표.
    xref: int  # fitz 의 이미지 참조 번호. 같은 이미지가 여러 페이지에 반복될 때 동일.


class PdfPage(BaseModel):
    """한 페이지 단위의 추출 결과."""

    page_number: int  # 0부터 시작.
    page_width: float  # 페이지 가로(포인트 단위).
    page_height: float  # 페이지 세로.
    text_blocks: list[TextBlock]
    image_blocks: list[ImageBlock]


class ParsedPortfolio(BaseModel):
    """텍스트 추출 + 노이즈 제거 후 다음 단계로 넘어가는 단위.

    이 시점에서는 아직 이미지 트리아지·구조화 전이라, 이미지 블록은 후보 전부를
    그대로 가지고 다닌다.
    """

    pages: list[PdfPage]


# 이미지 비중에 따른 처리 분기.
# - ``text_heavy``: 정보성 이미지가 임계 미만. 이미지 구조화 단계를 건너뛴다.
# - ``image_heavy``: 정보성 이미지가 임계 이상. 2차 LLM 트리아지 + 구조화 진행.
PdfBranch = Literal["text_heavy", "image_heavy"]


class TriagedPortfolio(BaseModel):
    """1차 이미지 트리아지 결과.

    - ``pages``: 각 페이지의 ``image_blocks`` 가 정보성 후보만 남도록 필터링됨.
      텍스트 블록은 변경 없음.
    - ``branch``: 다음 단계 분기.
    - ``info_img_count``: 트리아지 통과한 이미지의 총 개수(분기 판정 근거).
    """

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
    """비전 모델이 한 장에 대해 만든 구조화 정보."""

    source_page: int  # 0부터 시작하는 페이지 번호. 본문 삽입 위치 결정에 사용.
    image_type: str  # 유형(보통 ``ImageType`` 중 하나, 모델이 알 수 없는 값을 주면 그대로 보관).
    summary: str  # 한두 문장 한국어 요약.
    tech_signals: list[str]  # 이미지에서 추출된 기술 키워드.


class StructuredPortfolio(BaseModel):
    """비전 구조화까지 마친 결과. 통합 문서 병합 단계의 입력.

    - ``pages``: 텍스트 블록 보존(통합 문서 본문 작성용).
    - ``structured_images``: ``source_page`` 순서대로 본문에 끼워 넣을 후보.
    - ``branch``: text_heavy 면 ``structured_images`` 는 빈 리스트.
    """

    pages: list[PdfPage]
    structured_images: list[ImageStructure]
    branch: PdfBranch


class MergedDocument(BaseModel):
    """이미지까지 모두 텍스트화된 단일 통합 문서.

    이후 LLM 코드 탐색 세션의 입력으로 그대로 들어간다.
    """

    portfolio_text: str
    branch: PdfBranch


# 저장소 역할 라벨. unknown 은 판정 실패(LLM 이 코드 보고 직접 판단).
RepoRole = Literal["api_server", "ai_server", "frontend", "infra", "unknown"]


class RepoTree(BaseModel):
    """저장소 한 개의 트리 정보."""

    name: str  # 논리적 저장소 이름(RepoMeta.name 동일).
    role: RepoRole
    # 저장소 루트 기준 상대 경로 목록(블랙리스트 통과분), 정렬됨.
    file_paths: list[str]


class Stage3Result(BaseModel):
    """3단계 산출물.

    - ``repos``: 저장소별 역할 + 파일 목록(트리 렌더용).
    - ``tree_text``: 4단계 LLM 에 보낼, 역할 라벨이 붙은 사람이 읽기 좋은 트리 문자열.
    - ``path_index``: 4단계 ``read_files`` 가 경로 검증·실파일 조회에 쓰는 매핑.
      key 형식 ``"<repo_name>/<rel_path>"``, value 는 디스크 절대 경로.
    """

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


class RepositorySummary(BaseModel):
    """``project_summary.repositories[]`` 요소."""

    repo: str  # 저장소 이름 (예: ``order-api``)
    role: str  # 역할 라벨 (api_server / ai_server / frontend / infra / unknown)
    description: str  # 코드·포트폴리오 근거로 채운 한두 줄 설명


class CoreFeature(BaseModel):
    """``project_summary.core_features[]`` 요소."""

    name: str  # 기능 이름
    description: str  # 기능 설명
    based_on: list[str] = Field(default_factory=list)  # 근거 파일 경로(레포명/상대경로)


class ProjectSummary(BaseModel):
    """결과 첫 번째 블록 — 프로젝트 구조 및 핵심 기능."""

    overview: str
    repositories: list[RepositorySummary]
    core_features: list[CoreFeature]
    tech_stack: list[str]


class InterviewItem(BaseModel):
    """``interview[]`` 요소 — 질문 + 모범답변 + 근거."""

    id: int = Field(..., ge=1, le=5)  # 1~5 고정
    category: QuestionCategory
    question: str
    expected_answer: str
    # 근거 파일 경로(또는 ``["file_tree"]``) — 최소 1개 필수.
    # 추측 질문 방지 장치이므로 절대 비울 수 없다.
    based_on: list[str] = Field(..., min_length=1)


class InterviewQaResult(BaseModel):
    """파이프라인 최종 산출물."""

    project_summary: ProjectSummary
    interview: list[InterviewItem] = Field(..., min_length=5, max_length=5)


# ===== 콜백 페이로드 =====


class CallbackSuccess(BaseModel):
    """파이프라인 성공 시 callback_url 로 POST 되는 본문."""

    job_id: str
    status: Literal["succeeded"] = "succeeded"
    result: InterviewQaResult


class CallbackErrorDetail(BaseModel):
    """실패 페이로드의 ``error`` 필드."""

    status_code: int  # 422(잘못된 PDF), 403(private repo), 500(내부 오류) 등
    message: str  # 사용자에게 노출 가능한 한글 메시지


class CallbackFailure(BaseModel):
    """파이프라인 실패 시 callback_url 로 POST 되는 본문."""

    job_id: str
    status: Literal["failed"] = "failed"
    error: CallbackErrorDetail
