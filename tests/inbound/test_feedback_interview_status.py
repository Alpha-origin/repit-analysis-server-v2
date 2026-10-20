from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import BackgroundTasks

from tests.company_helpers import company_body, company_http
from tests.multi_helpers import feedback_body


@pytest.mark.parametrize("mode", ["solo", "multi"])
@pytest.mark.parametrize("with_company", [False, True])
@pytest.mark.parametrize("state", ["missing", None, "ABANDONED", "IN_PROGRESS", "UNKNOWN", "completed"])
async def test_incomplete_feedback_is_rejected_without_side_effects(
    mode: str, with_company: bool, state: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = feedback_body()
    if with_company:
        body["company"] = company_body()
    if state == "missing":
        del body["interviewStatus"]
    else:
        body["interviewStatus"] = state

    router = import_module(f"app.inbound.http.interview_feedback.{mode}.router")
    job_id = Mock()
    add_task = Mock()
    monkeypatch.setattr(router, "uuid", SimpleNamespace(uuid4=job_id))
    monkeypatch.setattr(BackgroundTasks, "add_task", add_task)

    async with company_http() as (client, webhook, llm):
        response = await client.post(f"/feedback/{mode}", json=body)

    assert response.status_code == 422
    assert any(error["loc"] == ["body", "interviewStatus"] for error in response.json()["detail"])
    job_id.assert_not_called()
    add_task.assert_not_called()
    assert llm.calls == []
    assert webhook.payloads == []


@pytest.mark.parametrize("mode", ["solo", "multi"])
@pytest.mark.parametrize("with_company", [False, True])
async def test_completed_feedback_keeps_grading_and_company_fit(mode: str, with_company: bool) -> None:
    body = feedback_body()
    if with_company:
        body["company"] = company_body()
    async with company_http() as (client, webhook, llm):
        response = await client.post(f"/feedback/{mode}", json=body)

    assert response.status_code == 202
    accepted = response.json()
    assert accepted["status"] == "accepted"
    assert accepted["sessionId"] == body["sessionId"]
    assert webhook.payloads[0]["jobId"] == accepted["jobId"]
    assert webhook.payloads[0]["status"] == "succeeded"
    assert (webhook.payloads[0]["result"]["companyFit"] is not None) is with_company
    assert len(llm.calls) == (2 if with_company else 1)


@pytest.mark.parametrize("mode", ["solo", "multi"])
@pytest.mark.parametrize("with_company", [False, True])
async def test_completed_status_does_not_enter_core_input(
    mode: str, with_company: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = feedback_body()
    if with_company:
        body["company"] = company_body()
    router = import_module(f"app.inbound.http.interview_feedback.{mode}.router")
    name = "FeedbackSoloRequest" if mode == "solo" else "FeedbackMultiRequest"
    core_factory = Mock(wraps=getattr(router, name))
    monkeypatch.setattr(router, name, core_factory)

    async with company_http() as (client, webhook, _):
        response = await client.post(f"/feedback/{mode}", json=body)

    assert response.status_code == 202
    assert webhook.payloads[0]["status"] == "succeeded"
    core_factory.assert_called_once()
    core_args = core_factory.call_args.kwargs
    assert "interview_status" not in core_args
    assert "interviewStatus" not in core_args
    assert core_args["session_id"] == body["sessionId"]
    assert core_args["interview_id"] == body["interviewId"]
    assert core_args["callback_url"] == body["callbackUrl"]
    assert core_args["questions"][0].question_id == body["questions"][0]["questionId"]
    assert core_args["answers"][0].content == body["answers"][0]["content"]
    assert (core_args["company"] is not None) is with_company
