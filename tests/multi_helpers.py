from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from dishka import Provider, Scope, make_async_container
from dishka.integrations.fastapi import setup_dishka
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.commands.dispatch_feedback_multi import DispatchFeedbackMulti
from app.core.commands.dispatch_question_tailor_multi import DispatchQuestionTailorMulti
from app.core.common.feedback.multi.answer_grading import MultiAnswerGrading
from app.core.common.feedback.solo.answer_assembly import AnswerAssembly
from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicCallResult
from app.core.common.question_tailor.multi.generate import MultiQuestionGenerate
from app.core.common.question_tailor.rewrite import QuestionRewrite
from app.inbound.http.interview_feedback.multi.router import make_feedback_multi_router
from app.inbound.http.question_tailor.multi.router import make_question_tailor_multi_router

ROLES = ["TECH", "HR", "CEO", "PM", "DESIGN", "NEW_ROLE"]


def tailor_body(count: int = 4, ids: tuple[int, int] = (1, 2)) -> dict[str, Any]:
    return {
        "interviewId": "iv-1",
        "userId": "u-1",
        "jobRole": "백엔드",
        "techPersona": {"personaId": "p-0", "role": "TECH"},
        "otherPersonas": [{"personaId": f"p-{index}", "role": ROLES[index]} for index in range(1, count)],
        "questions": [
            {
                "id": value,
                "category": "tech_choice",
                "question": "왜 선택했나요?",
                "expectedAnswer": "선택 근거",
                "basedOn": ["src/main.py"],
            }
            for value in ids
        ],
        "projectSummary": {"overview": "주문 서비스"},
        "callbackUrl": "https://example.com/callback",
    }


def generated_entry(index: int, number: int = 0) -> dict[str, Any]:
    return {
        "persona_index": index,
        "question": f"질문 {index}-{number}",
        "category": "collaboration",
        "expected_answer": "구체적 사례",
        "based_on": [],
    }


def tailor_outputs(counts: tuple[int, ...], ids: tuple[int, int] = (1, 2)) -> dict[str, dict[str, Any]]:
    return {
        "submit_generated_questions": {
            "questions": [
                generated_entry(index, number)
                for index, count in reversed(list(enumerate(counts, 1)))
                for number in range(count)
            ]
        },
        "submit_tailored_questions": {
            "questions": [{"id": value, "question": f"재작성 {value}"} for value in reversed(ids)]
        },
    }


def feedback_body(count: int = 4) -> dict[str, Any]:
    return {
        "sessionId": "s-1",
        "interviewId": "iv-1",
        "userId": "u-1",
        "personas": [{"personaId": f"p-{index}", "role": ROLES[index]} for index in range(count)],
        "questions": [
            {
                "questionId": "q-1",
                "personaId": "p-0",
                "type": "ORIGINAL",
                "content": "왜 선택했나요?",
                "intention": "선택 근거",
                "createdAt": "2026-09-15T00:00:00Z",
            }
        ],
        "answers": [
            {
                "answerId": "a-1",
                "questionId": "q-1",
                "content": "성능 요구 때문에 선택했습니다.",
                "createdAt": "2026-09-15T00:01:00Z",
            }
        ],
        "callbackUrl": "https://example.com/callback",
    }


def feedback_output(count: int = 4) -> dict[str, Any]:
    return {
        "overall": {
            "total_score": 80,
            "intent_alignment_score": 80,
            "reliability_score": 80,
            "summary": "총평",
            "strengths": [],
            "improvements": [],
        },
        "feedbacks": [{"question_id": "q-1", "model_answer": "예시 답변", "comment": "평가"}],
        "personas": [
            {
                "persona_id": f"p-{index}",
                "score": 80 if index == 0 else 0,
                "comment": "평가" if index == 0 else "담당 문항 없음",
            }
            for index in reversed(range(count))
        ],
    }


class StubTextClient:
    def __init__(self, outputs: dict[str, dict[str, Any]]) -> None:
        self.outputs = outputs

    async def call(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
        max_tokens: int = 4096,
    ) -> AnthropicCallResult:
        assert tool_choice is not None
        name = tool_choice["name"]
        return AnthropicCallResult(
            content_blocks=[{"type": "tool_use", "name": name, "input": self.outputs[name]}],
            input_tokens=10,
            output_tokens=10,
            stop_reason="tool_use",
        )


class RecordingWebhook:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    async def send(self, url: str, payload: dict[str, Any]) -> bool:
        self.payloads.append(payload)
        return True


@asynccontextmanager
async def multi_http(outputs: dict[str, dict[str, Any]]) -> AsyncIterator[tuple[AsyncClient, RecordingWebhook]]:
    llm = StubTextClient(outputs)
    webhook = RecordingWebhook()
    tailor = DispatchQuestionTailorMulti(
        webhook,
        MultiQuestionGenerate(llm, "stub", 3072, 600),
        QuestionRewrite(llm, "stub", 2048, 800),
    )
    feedback = DispatchFeedbackMulti(webhook, AnswerAssembly(), MultiAnswerGrading(llm, "stub", 14336, 3000), 10, 2)
    provider = Provider()
    provider.provide(lambda: tailor, provides=DispatchQuestionTailorMulti, scope=Scope.APP)
    provider.provide(lambda: feedback, provides=DispatchFeedbackMulti, scope=Scope.APP)
    container = make_async_container(provider)
    app = FastAPI()
    setup_dishka(container, app)
    app.include_router(make_question_tailor_multi_router())
    app.include_router(make_feedback_multi_router())
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, webhook
    finally:
        await container.close()
