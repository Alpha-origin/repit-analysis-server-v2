import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from dishka import Provider, make_async_container
from dishka.integrations.fastapi import setup_dishka
from fastapi import FastAPI

from app.core.common.interview_qa.ports.pdf_document_processor import PdfDocumentProcessor
from app.core.common.video.ports import Clock
from app.inbound.http.applicant_profile.router import make_applicant_profile_router
from app.inbound.http.audio.router import make_audio_router
from app.inbound.http.exception_handlers import register_exception_handlers
from app.inbound.http.interview_feedback.multi.router import make_feedback_multi_router
from app.inbound.http.interview_feedback.solo.router import make_feedback_solo_router
from app.inbound.http.question_cycle.router import make_question_cycle_router
from app.inbound.http.question_tailor.multi.router import make_question_tailor_multi_router
from app.inbound.http.question_tailor.router import make_question_tailor_router
from app.inbound.http.root_router import make_fastapi_root_router
from app.inbound.http.video.router import make_video_metrics_router, make_video_router
from app.main.audio_config import AudioSettings
from app.main.config import (
    AnthropicSettings,
    ApplicantProfileSettings,
    AppSettings,
    CallbackSecuritySettings,
    CompanyFitSettings,
    FeedbackMultiSettings,
    FeedbackScoringSettings,
    FeedbackSoloSettings,
    InterviewQaSettings,
    QuestionCycleSettings,
    QuestionTailorMultiSettings,
    QuestionTailorSettings,
    load_anthropic_settings,
    load_app_settings,
    load_applicant_profile_settings,
    load_callback_security_settings,
    load_company_fit_settings,
    load_feedback_multi_settings,
    load_feedback_solo_settings,
    load_interview_qa_settings,
    load_question_cycle_settings,
    load_question_tailor_multi_settings,
    load_question_tailor_settings,
)
from app.main.ioc.provider_registry import get_providers
from app.main.log_redaction import install_log_redaction
from app.main.security_bootstrap import check_pdf_source_hosts
from app.main.video_bootstrap import video_admission, video_repository, video_security
from app.main.video_config import VideoSettings
from app.outbound.adapters.audio.sqlite_repository import SqliteAudioRepository


def _setup_logging(level: str) -> None:
    logging.basicConfig(level=level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # 서명 URL 쿼리·토큰·userinfo 가 어떤 로그에도 남지 않도록 모든 핸들러에 필터를 건다.
    install_log_redaction()


def _make_lifespan() -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = app.state.dishka_container
        try:
            await container.get(PdfDocumentProcessor)
            yield
        finally:
            await container.close()

    return lifespan


def make_app(
    *di_providers: Provider,
    app_settings: AppSettings | None = None,
    anthropic_settings: AnthropicSettings | None = None,
    interview_qa_settings: InterviewQaSettings | None = None,
    feedback_solo_settings: FeedbackSoloSettings | None = None,
    feedback_multi_settings: FeedbackMultiSettings | None = None,
    question_tailor_settings: QuestionTailorSettings | None = None,
    question_tailor_multi_settings: QuestionTailorMultiSettings | None = None,
    applicant_profile_settings: ApplicantProfileSettings | None = None,
    question_cycle_settings: QuestionCycleSettings | None = None,
    audio_settings: AudioSettings | None = None,
    callback_security_settings: CallbackSecuritySettings | None = None,
    video_settings: VideoSettings | None = None,
    video_clock: Clock | None = None,
    company_fit_settings: CompanyFitSettings | None = None,
    feedback_scoring_settings: FeedbackScoringSettings | None = None,
) -> FastAPI:
    # 콜백 보안 설정을 가장 먼저 검증한다. production 에서 토큰이 없으면 어떤 부수효과도 없이 부팅 실패.
    if callback_security_settings is None:
        callback_security_settings = load_callback_security_settings()
    video = video_settings if video_settings is not None else VideoSettings()
    if app_settings is None:
        app_settings = load_app_settings()
    if anthropic_settings is None:
        anthropic_settings = load_anthropic_settings()
    if interview_qa_settings is None:
        interview_qa_settings = load_interview_qa_settings()
    if feedback_solo_settings is None:
        feedback_solo_settings = load_feedback_solo_settings()
    if feedback_multi_settings is None:
        feedback_multi_settings = load_feedback_multi_settings()
    if question_tailor_settings is None:
        question_tailor_settings = load_question_tailor_settings()
    if question_tailor_multi_settings is None:
        question_tailor_multi_settings = load_question_tailor_multi_settings()
    # make_app 복잡도 한도(C901) 때문에 audio 와 같은 조건식으로 둔다.
    profile_settings = (
        applicant_profile_settings if applicant_profile_settings is not None else load_applicant_profile_settings()
    )
    cycle_settings = question_cycle_settings if question_cycle_settings is not None else load_question_cycle_settings()

    _setup_logging(level=app_settings.LOGGING_LEVEL)
    check_pdf_source_hosts(interview_qa_settings, callback_security_settings)

    app = FastAPI(
        debug=app_settings.DEBUG_MODE,
        title=app_settings.SERVICE_NAME,
        version=app_settings.VERSION,
        lifespan=_make_lifespan(),
        root_path=app_settings.ROOT_PATH.rstrip("/"),
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    container = make_async_container(
        *get_providers(),
        *di_providers,
        # context 에 등록한 객체는 DI 컨테이너가 ``Scope.APP`` 으로 노출한다.
        # 각 단계 서비스/어댑터는 생성자에서 이 타입들을 주입받는다.
        context={
            AppSettings: app_settings,
            AnthropicSettings: anthropic_settings,
            InterviewQaSettings: interview_qa_settings,
            FeedbackScoringSettings: feedback_scoring_settings
            if feedback_scoring_settings is not None
            else FeedbackScoringSettings(),
            FeedbackSoloSettings: feedback_solo_settings,
            FeedbackMultiSettings: feedback_multi_settings,
            QuestionTailorSettings: question_tailor_settings,
            QuestionTailorMultiSettings: question_tailor_multi_settings,
            CallbackSecuritySettings: callback_security_settings,
            ApplicantProfileSettings: profile_settings,
            QuestionCycleSettings: cycle_settings,
            CompanyFitSettings: company_fit_settings
            if company_fit_settings is not None
            else load_company_fit_settings(),
        },
    )
    setup_dishka(container, app)

    # 도메인 예외 → HTTP 응답 매핑. PipelineError(422/403 등) 를 잡아
    # {"message": "..."} 형식의 JSON 으로 반환한다.
    register_exception_handlers(app)

    # 라우터 등록 — health, 면접 Q&A 본 엔드포인트, 그리고 콜백 수신측 테스트용 모킹 엔드포인트.
    app.include_router(make_fastapi_root_router())
    app.include_router(make_feedback_solo_router())
    app.include_router(make_feedback_multi_router())
    app.include_router(make_question_tailor_router())
    app.include_router(make_question_tailor_multi_router())
    app.include_router(make_applicant_profile_router())
    app.include_router(make_question_cycle_router())
    audio = audio_settings if audio_settings is not None else AudioSettings()
    if audio.enabled:
        app.include_router(
            make_audio_router(
                SqliteAudioRepository(audio.database_path, audio.max_attempts),
                audio.policy,
                audio.callback_hosts,
            )
        )
    _include_video_router(app, callback_security_settings, video, video_clock)
    return app


def _include_video_router(
    app: FastAPI, callback: CallbackSecuritySettings, video: VideoSettings, clock: Clock | None
) -> None:
    # VIDEO_ENABLED 는 라우터 등록만 제어한다. 켜져 있으면 VideoSettings 가 api_token 을 강제한다.
    if not video.enabled or video.api_token is None:
        return
    security = video_security(callback, video)
    repository = video_repository(video, clock)
    app.include_router(make_video_router(repository, video_admission(video, security), video.api_token))
    if video.metrics_enabled:
        app.include_router(make_video_metrics_router(repository, video.api_token))
