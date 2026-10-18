from collections.abc import Callable

from dishka import Provider, Scope, provide

from app.core.commands.dispatch_applicant_profile import DispatchApplicantProfile
from app.core.commands.dispatch_feedback_multi import DispatchFeedbackMulti
from app.core.commands.dispatch_feedback_solo import DispatchFeedbackSolo
from app.core.commands.dispatch_question_cycle import DispatchQuestionCycle
from app.core.commands.dispatch_question_tailor import DispatchQuestionTailor
from app.core.commands.dispatch_question_tailor_multi import DispatchQuestionTailorMulti
from app.core.common.applicant_profile.exploration import ProfileExploration
from app.core.common.applicant_profile.refine import ProfileEvidenceRefine
from app.core.common.company.fit.evaluate import CompanyFitEvaluation
from app.core.common.feedback.multi.answer_grading import MultiAnswerGrading
from app.core.common.feedback.solo.answer_assembly import AnswerAssembly
from app.core.common.feedback.solo.answer_grading import AnswerGrading
from app.core.common.interview_qa.pdf_page_transcription import PageTextTranscriber
from app.core.common.interview_qa.pdf_recovery_dto import RecoveryOptions
from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicTextClient
from app.core.common.interview_qa.ports.pdf_document_processor import PdfDocumentProcessor
from app.core.common.interview_qa.ports.webhook_client import WebhookClient
from app.core.common.interview_qa.stage1_validation import Stage1Validation
from app.core.common.interview_qa.stage2_document_merge import Stage2DocumentMerge
from app.core.common.interview_qa.stage2_image_llm_triage import Stage2ImageLlmTriage
from app.core.common.interview_qa.stage2_image_structuring import Stage2ImageStructuring
from app.core.common.interview_qa.stage2_image_triage import Stage2ImageTriage
from app.core.common.interview_qa.stage2_page_text_recovery import Stage2PageTextRecovery
from app.core.common.interview_qa.stage2_pdf_extract import Stage2PdfExtract
from app.core.common.interview_qa.stage2_text_clean import Stage2TextClean
from app.core.common.interview_qa.stage2_text_quality import Stage2TextQuality
from app.core.common.interview_qa.stage3_repo_tree import Stage3RepoTree
from app.core.common.interview_qa.stage4_file_reader import Stage4FileReader
from app.core.common.question_cycle.generate import QuestionCycleGenerate
from app.core.common.question_tailor.multi.generate import MultiQuestionGenerate
from app.core.common.question_tailor.rewrite import QuestionRewrite
from app.main.config import (
    AnthropicSettings,
    ApplicantProfileSettings,
    CompanyFitSettings,
    FeedbackMultiSettings,
    FeedbackSoloSettings,
    InterviewQaSettings,
    QuestionCycleSettings,
    QuestionTailorMultiSettings,
    QuestionTailorSettings,
)


class CoreProvider(Provider):
    scope = Scope.REQUEST

    # 1단계 — 입력 검증. 생성자 인자(Port) 는 OutboundProvider 와 자동으로 묶인다.
    stage1_validation = provide(Stage1Validation)

    # 2-5단계 — 통합 문서 병합. 외부 의존 없음.
    stage2_document_merge = provide(Stage2DocumentMerge)

    # 3단계 — GitHub 저장소 트리. GithubTarballFetcher Port 만 의존(OutboundProvider 가 제공).
    stage3_repo_tree = provide(Stage3RepoTree)

    @provide
    def stage4_file_reader(self, settings: InterviewQaSettings) -> Stage4FileReader:
        return Stage4FileReader(
            max_file_bytes=settings.MAX_FILE_BYTES,
            max_files_per_call=settings.MAX_FILES_PER_CALL,
        )

    # 종합 데이터 — Stage 1~3 을 거친 뒤 코드 탐색 루프로 재료를 모은다.
    @provide
    def profile_exploration(
        self,
        client: AnthropicTextClient,
        file_reader: Stage4FileReader,
        profile_settings: ApplicantProfileSettings,
        anthropic_settings: AnthropicSettings,
        pdf_settings: InterviewQaSettings,
    ) -> ProfileExploration:
        return ProfileExploration(
            client=client,
            file_reader=file_reader,
            text_model=anthropic_settings.TEXT_MODEL,
            max_turns=profile_settings.MAX_TURNS,
            token_limit=profile_settings.TOKEN_LIMIT,
            response_max_tokens=profile_settings.RESPONSE_MAX_TOKENS,
            document_recovery_enabled=pdf_settings.PDF_TEXT_RECOVERY_ENABLED,
        )

    # Stage 5' 근거 정리 — 외부 의존이 없어 생성자 인자도 없다.
    profile_refine = provide(ProfileEvidenceRefine)

    # /profile 진입점이 의존하는 백그라운드 작업 디스패처.
    dispatch_applicant_profile = provide(DispatchApplicantProfile)

    # 원질문 사이클 — 종합 데이터만 보고 모드별 질문 묶음을 만든다.
    @provide
    def question_cycle_generate(
        self,
        client: AnthropicTextClient,
        anthropic_settings: AnthropicSettings,
        cycle_settings: QuestionCycleSettings,
    ) -> QuestionCycleGenerate:
        return QuestionCycleGenerate(
            client=client,
            text_model=anthropic_settings.TEXT_MODEL,
            max_tokens=cycle_settings.MAX_TOKENS,
            retry=cycle_settings.RETRY,
            expected_answer_max_chars=cycle_settings.EXPECTED_ANSWER_MAX_CHARS,
            exclude_max=cycle_settings.EXCLUDE_MAX,
            text_max_chars=cycle_settings.TEXT_MAX_CHARS,
        )

    # /questions/cycle 진입점이 의존하는 백그라운드 작업 디스패처.
    dispatch_question_cycle = provide(DispatchQuestionCycle)

    # 피드백(1:1) — 조립은 외부 의존이 없어 생성자 인자도 없다.
    answer_assembly = provide(AnswerAssembly)

    @provide
    def answer_grading(
        self,
        client: AnthropicTextClient,
        anthropic_settings: AnthropicSettings,
        feedback_settings: FeedbackSoloSettings,
    ) -> AnswerGrading:
        return AnswerGrading(
            client=client,
            text_model=anthropic_settings.TEXT_MODEL,
            max_tokens=feedback_settings.GRADING_MAX_TOKENS,
            answer_max_chars=feedback_settings.ANSWER_MAX_CHARS,
        )

    @provide
    def company_fit_factory(
        self,
        client: AnthropicTextClient,
        anthropic_settings: AnthropicSettings,
        fit_settings: CompanyFitSettings,
    ) -> Callable[[int], CompanyFitEvaluation]:
        def build(answer_max_chars: int) -> CompanyFitEvaluation:
            return CompanyFitEvaluation(
                client=client,
                text_model=fit_settings.MODEL or anthropic_settings.TEXT_MODEL,
                max_tokens=fit_settings.MAX_TOKENS,
                temperature=fit_settings.TEMPERATURE,
                answer_max_chars=answer_max_chars,
            )

        return build

    @provide
    def company_fit_evaluation(
        self,
        factory: Callable[[int], CompanyFitEvaluation],
        feedback_settings: FeedbackSoloSettings,
    ) -> CompanyFitEvaluation:
        return factory(feedback_settings.ANSWER_MAX_CHARS)

    # /feedback/solo 진입점이 의존하는 백그라운드 작업 디스패처.
    @provide
    def dispatch_feedback_solo(
        self,
        webhook: WebhookClient,
        answer_assembly: AnswerAssembly,
        answer_grading: AnswerGrading,
        feedback_settings: FeedbackSoloSettings,
        company_fit: CompanyFitEvaluation,
    ) -> DispatchFeedbackSolo:
        return DispatchFeedbackSolo(
            webhook=webhook,
            answer_assembly=answer_assembly,
            answer_grading=answer_grading,
            company_fit=company_fit,
            frequent_word_top_n=feedback_settings.FREQUENT_WORD_TOP_N,
            frequent_word_min_count=feedback_settings.FREQUENT_WORD_MIN_COUNT,
        )

    # 피드백(N:1) — 조립은 solo 와 같은 AnswerAssembly 를 그대로 쓴다(단일 세션이라 체인 구조가 같다).
    @provide
    def multi_answer_grading(
        self,
        client: AnthropicTextClient,
        anthropic_settings: AnthropicSettings,
        feedback_settings: FeedbackMultiSettings,
    ) -> MultiAnswerGrading:
        return MultiAnswerGrading(
            client=client,
            text_model=anthropic_settings.TEXT_MODEL,
            max_tokens=feedback_settings.GRADING_MAX_TOKENS,
            answer_max_chars=feedback_settings.ANSWER_MAX_CHARS,
        )

    # /feedback/multi 진입점이 의존하는 백그라운드 작업 디스패처.
    @provide
    def dispatch_feedback_multi(
        self,
        webhook: WebhookClient,
        answer_assembly: AnswerAssembly,
        multi_answer_grading: MultiAnswerGrading,
        feedback_settings: FeedbackMultiSettings,
        factory: Callable[[int], CompanyFitEvaluation],
    ) -> DispatchFeedbackMulti:
        return DispatchFeedbackMulti(
            webhook=webhook,
            answer_assembly=answer_assembly,
            answer_grading=multi_answer_grading,
            company_fit=factory(feedback_settings.ANSWER_MAX_CHARS),
            frequent_word_top_n=feedback_settings.FREQUENT_WORD_TOP_N,
            frequent_word_min_count=feedback_settings.FREQUENT_WORD_MIN_COUNT,
        )

    # 질문 재작성 — 면접 시작 전 원질문을 사전 정보에 맞게 다시 쓴다.
    @provide
    def question_rewrite(
        self,
        client: AnthropicTextClient,
        anthropic_settings: AnthropicSettings,
        tailor_settings: QuestionTailorSettings,
    ) -> QuestionRewrite:
        return QuestionRewrite(
            client=client,
            text_model=anthropic_settings.TEXT_MODEL,
            max_tokens=tailor_settings.REWRITE_MAX_TOKENS,
            question_max_chars=tailor_settings.QUESTION_MAX_CHARS,
            spoken_text_max_chars=tailor_settings.SPOKEN_TEXT_MAX_CHARS,
        )

    # /questions/tailor 진입점이 의존하는 백그라운드 작업 디스패처.
    dispatch_question_tailor = provide(DispatchQuestionTailor)

    # N:1 질문 생성 — 비개발 면접관이 물을 질문을 새로 만든다.
    @provide
    def multi_question_generate(
        self,
        client: AnthropicTextClient,
        anthropic_settings: AnthropicSettings,
        multi_settings: QuestionTailorMultiSettings,
    ) -> MultiQuestionGenerate:
        return MultiQuestionGenerate(
            client=client,
            text_model=anthropic_settings.TEXT_MODEL,
            max_tokens=multi_settings.GENERATE_MAX_TOKENS,
            text_max_chars=multi_settings.TEXT_MAX_CHARS,
            spoken_text_max_chars=multi_settings.SPOKEN_TEXT_MAX_CHARS,
        )

    # /questions/tailor/multi 진입점이 의존하는 백그라운드 작업 디스패처.
    # 리텍스팅은 solo 와 같은 QuestionRewrite 를 그대로 재사용한다.
    # 문항 수는 서버 설정이 아니라 요청 데이터라서 생성자 인자가 없다.
    dispatch_question_tailor_multi = provide(DispatchQuestionTailorMulti)

    @provide
    def stage2_pdf_extract(self, processor: PdfDocumentProcessor) -> Stage2PdfExtract:
        return Stage2PdfExtract(processor)

    stage2_text_quality = provide(Stage2TextQuality)

    @provide
    def recovery_options(self, settings: InterviewQaSettings) -> RecoveryOptions:
        return RecoveryOptions(
            enabled=settings.PDF_TEXT_RECOVERY_ENABLED,
            max_pages=settings.PDF_TEXT_RECOVERY_MAX_PAGES,
            max_calls=settings.PDF_TEXT_RECOVERY_MAX_CALLS,
            concurrency=settings.PDF_TEXT_RECOVERY_CONCURRENCY,
            max_tokens=settings.PDF_TEXT_RECOVERY_MAX_TOKENS,
            call_timeout_seconds=settings.PDF_TEXT_RECOVERY_CALL_TIMEOUT_SECONDS,
            total_timeout_seconds=settings.PDF_TEXT_RECOVERY_TOTAL_TIMEOUT_SECONDS,
            render_dpi=settings.PDF_TEXT_RECOVERY_RENDER_DPI,
            render_max_pixels=settings.PDF_TEXT_RECOVERY_RENDER_MAX_PIXELS,
            max_tiles_per_page=settings.PDF_TEXT_RECOVERY_MAX_TILES_PER_PAGE,
            vision_long_edge_px=settings.VISION_RESIZE_LONG_EDGE_PX,
        )

    @provide
    def page_text_transcriber(
        self,
        client: AnthropicTextClient,
        settings: AnthropicSettings,
        options: RecoveryOptions,
    ) -> PageTextTranscriber:
        return PageTextTranscriber(client, settings.VISION_MODEL, options)

    stage2_page_text_recovery = provide(Stage2PageTextRecovery)

    @provide
    def stage2_text_clean(self, settings: InterviewQaSettings) -> Stage2TextClean:
        return Stage2TextClean(
            header_footer_min_ratio=settings.HEADER_FOOTER_MIN_RATIO,
            toc_front_pages=settings.TOC_FRONT_PAGES,
        )

    @provide
    def stage2_image_triage(self, settings: InterviewQaSettings) -> Stage2ImageTriage:
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
