from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from dishka import Provider, Scope, make_async_container
from dishka.integrations.fastapi import setup_dishka
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.commands.dispatch_feedback_multi import DispatchFeedbackMulti
from app.core.commands.dispatch_feedback_solo import DispatchFeedbackSolo
from app.core.commands.dispatch_question_tailor import DispatchQuestionTailor
from app.core.commands.dispatch_question_tailor_multi import DispatchQuestionTailorMulti
from app.core.common.company.dto import CompanyContext
from app.core.common.company.fit.evaluate import CompanyFitEvaluation
from app.core.common.feedback.multi.answer_grading import MultiAnswerGrading
from app.core.common.feedback.solo.answer_assembly import AnswerAssembly
from app.core.common.feedback.solo.answer_grading import AnswerGrading
from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicCallResult
from app.core.common.question_tailor.multi.generate import MultiQuestionGenerate
from app.core.common.question_tailor.rewrite import QuestionRewrite
from app.inbound.http.company_dto import CompanyContextRequest
from app.inbound.http.interview_feedback.multi.router import make_feedback_multi_router
from app.inbound.http.interview_feedback.solo.router import make_feedback_solo_router
from app.inbound.http.question_tailor.multi.router import make_question_tailor_multi_router
from app.inbound.http.question_tailor.router import make_question_tailor_router
from tests.multi_helpers import RecordingWebhook, StubTextClient, feedback_output, tailor_outputs


def company_body() -> dict[str, Any]:
    return {
        "companyId": "3",
        "displayName": "샘플커머스",
        "category": "CONSUMER",
        "facts": [
            {"factId": "b1", "kind": "BUSINESS", "title": "샘플마켓", "content": "온라인 거래 서비스"},
            {"factId": "p1", "kind": "PROBLEM", "title": "재고 초과 판매", "content": "동시 주문 처리 사례"},
            {
                "factId": "t1",
                "kind": "TALENT",
                "title": "오너십",
                "content": "결과까지 책임진다",
                "positiveSignals": ["문제를 직접 정의한 경험"],
                "concernSignals": ["책임을 돌린 경험"],
            },
        ],
        "interviewerBrief": "샘플커머스는 온라인 거래와 오너십을 중요하게 보는 가상의 기업이다",
    }


def company_context() -> CompanyContext:
    return CompanyContextRequest.model_validate(company_body()).to_core()


def fit_output() -> dict[str, Any]:
    return {
        "comment": "샘플커머스가 중요하게 보는 오너십 관점에서 문제 해결 경험이 드러났습니다",
        "matched_focus": [{"fact_id": "t1", "evidence": "문제를 직접 해결했습니다", "question_ids": ["q-1"]}],
        "gaps": [{"fact_id": "p1", "suggestion": "동시 요청의 처리 근거를 설명해 보세요", "question_ids": ["q-1"]}],
    }


class RecordingCompanyClient(StubTextClient):
    def __init__(self) -> None:
        super().__init__(
            {
                **tailor_outputs((2, 2, 2)),
                "submit_feedback": feedback_output(),
                "submit_multi_feedback": feedback_output(),
                "submit_company_fit": fit_output(),
            }
        )
        self.calls: list[dict[str, Any]] = []
        self.fail_fit = False
        self.fail_grading = False

    async def call(self, **kwargs: Any) -> AnthropicCallResult:
        self.calls.append(kwargs.copy())
        name = kwargs["tool_choice"].get("name")
        if self.fail_fit and name == "submit_company_fit":
            raise RuntimeError("기업 코멘트 생성 실패")
        if self.fail_grading and name != "submit_company_fit":
            raise RuntimeError("채점 실패")
        kwargs.pop("temperature", None)
        return await super().call(**kwargs)


@asynccontextmanager
async def company_http() -> AsyncIterator[tuple[AsyncClient, RecordingWebhook, RecordingCompanyClient]]:
    llm = RecordingCompanyClient()
    webhook = RecordingWebhook()
    fit = CompanyFitEvaluation(llm, "stub", 1500, 0.2, 3000)
    rewrite = QuestionRewrite(llm, "stub", 3072, 800, 600)
    solo = DispatchFeedbackSolo(webhook, AnswerAssembly(), AnswerGrading(llm, "stub", 4096, 3000), 10, 2, fit)
    multi = DispatchFeedbackMulti(webhook, AnswerAssembly(), MultiAnswerGrading(llm, "stub", 4096, 3000), 10, 2, fit)
    tailor = DispatchQuestionTailor(webhook, rewrite)
    tailor_multi = DispatchQuestionTailorMulti(webhook, MultiQuestionGenerate(llm, "stub", 4608, 600, 600), rewrite)
    provider = Provider()
    provider.provide(lambda: solo, provides=DispatchFeedbackSolo, scope=Scope.APP)
    provider.provide(lambda: multi, provides=DispatchFeedbackMulti, scope=Scope.APP)
    provider.provide(lambda: tailor, provides=DispatchQuestionTailor, scope=Scope.APP)
    provider.provide(lambda: tailor_multi, provides=DispatchQuestionTailorMulti, scope=Scope.APP)
    container = make_async_container(provider)
    app = FastAPI()
    setup_dishka(container, app)
    for router in [
        make_feedback_solo_router(),
        make_feedback_multi_router(),
        make_question_tailor_router(),
        make_question_tailor_multi_router(),
    ]:
        app.include_router(router)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, webhook, llm
    finally:
        await container.close()
