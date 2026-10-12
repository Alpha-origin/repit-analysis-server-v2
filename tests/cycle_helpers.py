from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from dishka import Provider, Scope, make_async_container
from dishka.integrations.fastapi import setup_dishka
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.commands.dispatch_question_cycle import DispatchQuestionCycle
from app.core.common.applicant_profile.dto import ApplicantProfile
from app.core.common.applicant_profile.refine import ProfileEvidenceRefine
from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicCallResult
from app.core.common.question_cycle.generate import QuestionCycleGenerate
from app.inbound.http.question_cycle.router import make_question_cycle_router
from tests.multi_helpers import RecordingWebhook
from tests.profile_helpers import PATH_INDEX, raw_profile

# 카테고리마다 profile 에서 고를 근거 경로.
BASED_ON = {
    "tech_choice": "order-api/build.gradle",
    "implementation": "order-api/src/main/java/order/StockService.java",
    "troubleshooting": "order-api/src/main/java/order/RedisLockConfig.java",
    "integration": "order-api/src/main/java/order/PaymentClient.java",
    "structure": "order-api/src/main/java/order/",  # 끝 슬래시는 검증 전에 지운다.
}
ALL_CATEGORIES = ("tech_choice", "implementation", "troubleshooting", "integration", "structure")


def refined_profile(drop: Sequence[str] = ()) -> ApplicantProfile:
    raw = raw_profile()
    for key in drop:
        raw[key] = []
    return ProfileEvidenceRefine().execute(raw, PATH_INDEX, "컴퓨터공학")


def entry(set_no: int, category: str, based_on: str | None = None) -> dict[str, Any]:
    return {
        "set_no": set_no,
        "category": category,
        "question": f"세트 {set_no} {category} 질문",
        "intention": f"{category} 를 설명할 수 있는지",
        "expected_answer": "모범답안",
        "based_on": [based_on or BASED_ON[category]],
    }


def solo_entries(categories: Sequence[str] = ALL_CATEGORIES) -> list[dict[str, Any]]:
    # 세트마다 카테고리를 하나씩 넣고, 남는 칸은 첫 카테고리로 채운다.
    filled = [*categories, *[categories[0]] * (5 - len(categories))]
    return [entry(set_no, category) for set_no in (3, 2, 1) for category in reversed(filled)]


def multi_entries() -> list[dict[str, Any]]:
    pairs = [("tech_choice", "implementation"), ("troubleshooting", "integration"), ("structure", "implementation")]
    return [entry(set_no, category) for set_no, pair in enumerate(pairs, 1) for category in pair]


def cycle_body(mode: str = "SOLO", profile: ApplicantProfile | None = None) -> dict[str, Any]:
    return {
        "mode": mode,
        "profile": (profile or refined_profile()).model_dump(by_alias=True),
        "excludeQuestions": ["예전 질문", "  "],
        "callbackUrl": "https://example.com/callback",
    }


class SequencedClient:
    """submit_question_cycle 제출을 차례로 돌려준다. 받은 메시지는 남겨 둔다."""

    def __init__(self, submissions: list[dict[str, Any]]) -> None:
        self.submissions = submissions
        self.calls: list[dict[str, Any]] = []

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
        self.calls.append({"messages": list(messages), "tools": tools})
        submission = self.submissions[len(self.calls) - 1]
        return AnthropicCallResult(
            content_blocks=[
                {"type": "tool_use", "id": f"t-{len(self.calls)}", "name": "submit_question_cycle", "input": submission}
            ],
            input_tokens=10,
            output_tokens=10,
            stop_reason="tool_use",
        )


@asynccontextmanager
async def cycle_http(
    submissions: list[dict[str, Any]],
) -> AsyncIterator[tuple[AsyncClient, RecordingWebhook, SequencedClient]]:
    llm = SequencedClient(submissions)
    webhook = RecordingWebhook()
    dispatcher = DispatchQuestionCycle(webhook, QuestionCycleGenerate(llm, "stub", 12288, 1, 400, 30, 600))
    provider = Provider()
    provider.provide(lambda: dispatcher, provides=DispatchQuestionCycle, scope=Scope.APP)
    container = make_async_container(provider)
    app = FastAPI()
    setup_dishka(container, app)
    app.include_router(make_question_cycle_router())
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, webhook, llm
    finally:
        await container.close()
