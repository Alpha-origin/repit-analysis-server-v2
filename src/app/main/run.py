import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from dishka import Provider, make_async_container
from dishka.integrations.fastapi import setup_dishka
from fastapi import FastAPI

from app.inbound.http.exception_handlers import register_exception_handlers
from app.inbound.http.interview_qa.mock_router import make_interview_qa_mock_router
from app.inbound.http.interview_qa.router import make_interview_qa_router
from app.inbound.http.root_router import make_fastapi_root_router
from app.main.config import (
    AnthropicSettings,
    AppSettings,
    InterviewQaSettings,
    load_anthropic_settings,
    load_app_settings,
    load_interview_qa_settings,
)
from app.main.ioc.provider_registry import get_providers


def _setup_logging(level: str) -> None:
    """루트 로거를 표준 포맷으로 설정한다."""
    logging.basicConfig(level=level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")


def _make_lifespan() -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """FastAPI lifespan 컨텍스트 — Dishka 컨테이너를 종료 시 정리한다."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = app.state.dishka_container
        try:
            yield
        finally:
            await container.close()

    return lifespan


def make_app(
    *di_providers: Provider,
    app_settings: AppSettings | None = None,
    anthropic_settings: AnthropicSettings | None = None,
    interview_qa_settings: InterviewQaSettings | None = None,
) -> FastAPI:
    """FastAPI 앱 팩토리.

    테스트에서는 ``di_providers`` 로 outbound 어댑터를 fake 로 교체하고,
    ``anthropic_settings`` 를 직접 넣어 ``ANTHROPIC_API_KEY`` env 없이 부팅 가능.
    """
    if app_settings is None:
        app_settings = load_app_settings()
    if anthropic_settings is None:
        anthropic_settings = load_anthropic_settings()
    if interview_qa_settings is None:
        interview_qa_settings = load_interview_qa_settings()

    _setup_logging(level=app_settings.LOGGING_LEVEL)

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
        },
    )
    setup_dishka(container, app)

    # 도메인 예외 → HTTP 응답 매핑. PipelineError(422/403 등) 를 잡아
    # {"message": "..."} 형식의 JSON 으로 반환한다.
    register_exception_handlers(app)

    # 라우터 등록 — health, 면접 Q&A 본 엔드포인트, 그리고 콜백 수신측 테스트용 모킹 엔드포인트.
    app.include_router(make_fastapi_root_router())
    app.include_router(make_interview_qa_router())
    app.include_router(make_interview_qa_mock_router())
    return app
