import asyncio
import logging
from typing import Any

import pytest

from app.core.common.company.fit.evaluate import CompanyFitEvaluation
from app.core.common.company.fit.logging_context import company_fit_job
from app.core.common.feedback.solo.dto import GradingTarget
from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicCallResult, AnthropicTextClientError
from tests.company_helpers import RecordingCompanyClient, company_context, fit_output


def target(question_id: str = "q-1") -> GradingTarget:
    return GradingTarget(
        question_id=question_id, type="ORIGINAL", intention="선택 근거", content="질문", answer="비공개 답변"
    )


def response(raw: dict[str, Any], stop: str = "tool_use") -> AnthropicCallResult:
    return AnthropicCallResult(
        content_blocks=[{"type": "tool_use", "name": "submit_company_fit", "input": raw}],
        input_tokens=10,
        output_tokens=20,
        stop_reason=stop,
    )


async def test_invalid_submission_is_retried_once_and_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    client = RecordingCompanyClient()
    calls: list[dict[str, Any]] = []

    async def call(**kwargs: Any) -> AnthropicCallResult:
        calls.append(kwargs)
        raw = fit_output()
        if len(calls) == 1:
            raw["comment"] = ""
        return response(raw)

    monkeypatch.setattr(client, "call", call)
    result = await CompanyFitEvaluation(client, "stub", 1500, 0.2, 3000).execute((target(),), company_context(), None)
    assert result is not None
    assert len(calls) == 2
    assert calls[0]["tools"][0]["strict"] is True
    assert "직전 응답" in calls[1]["messages"][0]["content"]


async def test_api_error_does_not_trigger_application_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    client = RecordingCompanyClient()
    calls = 0

    async def call(**kwargs: Any) -> AnthropicCallResult:
        nonlocal calls
        calls += 1
        raise AnthropicTextClientError("비공개 오류", kind="transient")

    monkeypatch.setattr(client, "call", call)
    assert (
        await CompanyFitEvaluation(client, "stub", 1500, 0.2, 3000).execute((target(),), company_context(), None)
        is None
    )
    assert calls == 1


async def test_retry_shares_deadline_and_cleans_up_call(monkeypatch: pytest.MonkeyPatch) -> None:
    client = RecordingCompanyClient()
    calls, cancelled = 0, asyncio.Event()

    async def call(**kwargs: Any) -> AnthropicCallResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            raw = fit_output()
            raw["comment"] = ""
            return response(raw)
        try:
            await asyncio.Future[None]()
        finally:
            cancelled.set()
        raise AssertionError("unreachable")

    monkeypatch.setattr(client, "call", call)
    evaluator = CompanyFitEvaluation(client, "stub", 1500, 0.2, 3000, timeout_seconds=0.02)
    assert await asyncio.wait_for(evaluator.execute((target(),), company_context(), None), 1) is None
    assert calls == 2
    assert cancelled.is_set()


async def test_company_references_keep_fact_and_question_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    client = RecordingCompanyClient()
    raw = fit_output()
    raw["comment"] = "Q62 답변의 근거"
    for field in ("matched_focus", "gaps"):
        raw[field][0]["question_ids"] = ["62"]
    raw["matched_focus"][0]["evidence"] = "문항 62 근거"

    async def call(**kwargs: Any) -> AnthropicCallResult:
        return response(raw)

    monkeypatch.setattr(client, "call", call)
    result = await CompanyFitEvaluation(client, "stub", 1500, 0.2, 3000).execute(
        (target("62"),), company_context(), None
    )
    assert result is not None
    assert result.comment == "Q1 답변의 근거"
    assert result.matched_focus[0].evidence == "문항 1 근거"
    assert result.matched_focus[0].fact_id == "t1"
    assert result.matched_focus[0].question_ids == ["62"]


async def test_concurrent_logs_keep_job_context_and_do_not_leak_text(caplog: pytest.LogCaptureFixture) -> None:
    client = RecordingCompanyClient()

    async def run(job_id: str) -> None:
        with company_fit_job(job_id):
            await asyncio.sleep(0)
            await CompanyFitEvaluation(client, "stub", 1500, 0.2, 3000).execute((target(),), company_context(), None)

    with caplog.at_level(logging.INFO):
        await asyncio.gather(run("job-a"), run("job-b"))
        await CompanyFitEvaluation(client, "stub", 1500, 0.2, 3000).execute((target(),), company_context(), None)
    records = [record for record in caplog.records if record.getMessage().startswith("company_fit.completed")]
    assert [getattr(record, "job_id", None) for record in records] == ["job-a", "job-b", None]
    assert "job_id=job-a" in records[0].getMessage()
    assert "비공개 답변" not in caplog.text


@pytest.mark.parametrize("second_stop", ["tool_use", "max_tokens"])
async def test_truncated_submission_gets_one_compact_retry(second_stop: str, monkeypatch: pytest.MonkeyPatch) -> None:
    client = RecordingCompanyClient()
    calls: list[dict[str, Any]] = []

    async def call(**kwargs: Any) -> AnthropicCallResult:
        calls.append(kwargs)
        return response(fit_output(), "max_tokens" if len(calls) == 1 else second_stop)

    monkeypatch.setattr(client, "call", call)
    result = await CompanyFitEvaluation(client, "stub", 4096, 0.2, 3000).execute((target(),), company_context(), None)
    assert (result is not None) == (second_stop == "tool_use")
    assert len(calls) == 2
    assert "각각 최대 1개" in calls[1]["messages"][0]["content"]
    assert all(call["max_tokens"] == 4096 for call in calls)


@pytest.mark.parametrize("known", [True, False])
async def test_display_prefixed_fact_ids_require_a_registered_fact(
    known: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = RecordingCompanyClient()
    raw = fit_output()
    for key in ("matched_focus", "gaps"):
        for item in raw[key]:
            item["fact_id"] = "f:" + (item["fact_id"] if known else "unknown")

    async def call(**kwargs: Any) -> AnthropicCallResult:
        return response(raw)

    monkeypatch.setattr(client, "call", call)
    result = await CompanyFitEvaluation(client, "stub", 4096, 0.2, 3000).execute((target(),), company_context(), None)
    if known:
        assert result is not None
        assert result.matched_focus[0].fact_id == "t1"
        assert result.gaps[0].fact_id == "p1"
    else:
        assert result is None
