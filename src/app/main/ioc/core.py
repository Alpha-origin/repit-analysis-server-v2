from dishka import Provider, Scope, provide

from app.core.commands.dispatch_interview_qa import DispatchInterviewQa
from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicTextClient
from app.core.common.interview_qa.stage1_validation import Stage1Validation
from app.core.common.interview_qa.stage2_document_merge import Stage2DocumentMerge
from app.core.common.interview_qa.stage2_image_llm_triage import Stage2ImageLlmTriage
from app.core.common.interview_qa.stage2_image_structuring import Stage2ImageStructuring
from app.core.common.interview_qa.stage2_image_triage import Stage2ImageTriage
from app.core.common.interview_qa.stage2_pdf_extract import Stage2PdfExtract
from app.core.common.interview_qa.stage3_repo_tree import Stage3RepoTree
from app.core.common.interview_qa.stage4_file_reader import Stage4FileReader
from app.core.common.interview_qa.stage4_llm_session import Stage4LlmSession
from app.main.config import AnthropicSettings, InterviewQaSettings


class CoreProvider(Provider):
    """Command/Query 핸들러 + 도메인 서비스를 등록하는 Provider."""

    scope = Scope.REQUEST

    # 1단계 — 입력 검증. 생성자 인자(Port) 는 OutboundProvider 와 자동으로 묶인다.
    stage1_validation = provide(Stage1Validation)

    # 2-5단계 — 통합 문서 병합. 외부 의존 없음.
    stage2_document_merge = provide(Stage2DocumentMerge)

    # 3단계 — GitHub 저장소 트리. GithubTarballFetcher Port 만 의존(OutboundProvider 가 제공).
    stage3_repo_tree = provide(Stage3RepoTree)

    @provide
    def stage4_file_reader(self, settings: InterviewQaSettings) -> Stage4FileReader:
        """4단계 — read_files 도구 본문 처리 + 토큰 절감."""
        return Stage4FileReader(
            max_file_bytes=settings.MAX_FILE_BYTES,
            max_files_per_call=settings.MAX_FILES_PER_CALL,
        )

    @provide
    def stage4_llm_session(
        self,
        client: AnthropicTextClient,
        file_reader: Stage4FileReader,
        interview_qa_settings: InterviewQaSettings,
        anthropic_settings: AnthropicSettings,
    ) -> Stage4LlmSession:
        """4단계 — LLM 탐색 세션 오케스트레이션."""
        return Stage4LlmSession(
            client=client,
            file_reader=file_reader,
            text_model=anthropic_settings.TEXT_MODEL,
            max_turns=interview_qa_settings.MAX_TURNS,
            token_limit=interview_qa_settings.TOKEN_LIMIT,
            response_max_tokens=interview_qa_settings.STAGE4_RESPONSE_MAX_TOKENS,
        )

    # /generate 진입점이 의존하는 백그라운드 작업 디스패처.
    dispatch_interview_qa = provide(DispatchInterviewQa)

    @provide
    def stage2_pdf_extract(self, settings: InterviewQaSettings) -> Stage2PdfExtract:
        """2-1단계 — PDF 텍스트·이미지 추출 + 노이즈 제거 서비스."""
        return Stage2PdfExtract(
            header_footer_min_ratio=settings.HEADER_FOOTER_MIN_RATIO,
            toc_front_pages=settings.TOC_FRONT_PAGES,
        )

    @provide
    def stage2_image_triage(self, settings: InterviewQaSettings) -> Stage2ImageTriage:
        """2-2단계 — 규칙 기반 1차 이미지 트리아지 + 분기 판정."""
        return Stage2ImageTriage(
            min_px=settings.IMG_TRIAGE_MIN_PX,
            max_aspect_ratio=settings.IMG_TRIAGE_MAX_RATIO,
            min_area_ratio=settings.IMG_TRIAGE_MIN_AREA_RATIO,
            info_img_threshold=settings.INFO_IMG_THRESHOLD,
            repeated_min_ratio=settings.HEADER_FOOTER_MIN_RATIO,
        )

    @provide
    def stage2_image_llm_triage(
        self,
        client: AnthropicTextClient,
        interview_qa_settings: InterviewQaSettings,
        anthropic_settings: AnthropicSettings,
    ) -> Stage2ImageLlmTriage:
        """2-3단계 — image_heavy 분기 전용 LLM 2차 트리아지."""
        return Stage2ImageLlmTriage(
            client=client,
            text_model=anthropic_settings.TEXT_MODEL,
            max_tokens=interview_qa_settings.LLM_TRIAGE_MAX_TOKENS,
            context_vertical_px=interview_qa_settings.LLM_TRIAGE_CONTEXT_VERTICAL_PX,
            context_max_chars=interview_qa_settings.LLM_TRIAGE_CONTEXT_MAX_CHARS,
        )

    @provide
    def stage2_image_structuring(
        self,
        client: AnthropicTextClient,
        interview_qa_settings: InterviewQaSettings,
        anthropic_settings: AnthropicSettings,
    ) -> Stage2ImageStructuring:
        """2-4단계 — 비전 호출 + tool-use 로 이미지 구조화 정보 수집."""
        return Stage2ImageStructuring(
            client=client,
            vision_model=anthropic_settings.VISION_MODEL,
            max_tokens=interview_qa_settings.VISION_STRUCTURING_MAX_TOKENS,
            concurrency=interview_qa_settings.VISION_CONCURRENCY,
            max_images_per_request=interview_qa_settings.VISION_MAX_IMAGES_PER_REQUEST,
            resize_long_edge_px=interview_qa_settings.VISION_RESIZE_LONG_EDGE_PX,
            context_vertical_px=interview_qa_settings.LLM_TRIAGE_CONTEXT_VERTICAL_PX,
            context_max_chars=interview_qa_settings.LLM_TRIAGE_CONTEXT_MAX_CHARS,
        )
