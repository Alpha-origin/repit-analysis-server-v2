from typing import Any

import pytest
from pydantic import ValidationError

from app.inbound.http.company_dto import CompanyContextRequest
from tests.company_helpers import company_body, company_http
from tests.multi_helpers import feedback_body, tailor_body

ENDPOINTS = ["/questions/tailor", "/questions/tailor/multi", "/feedback/solo", "/feedback/multi"]
FIELDS = ["companyId", "displayName", "category", "interviewerBrief", "factId", "title", "content", "signal"]


def _set_text(body: dict[str, Any], field: str, text: str) -> None:
    if field in {"factId", "title", "content"}:
        body["facts"][0][field] = text
    elif field == "signal":
        body["facts"][2]["positiveSignals"] = [text]
    else:
        body[field] = text


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("field", FIELDS)
async def test_blank_company_text_is_rejected_before_job_acceptance(endpoint: str, field: str) -> None:
    body = tailor_body() if "tailor" in endpoint else feedback_body()
    if endpoint == "/questions/tailor":
        body["profile"] = {"jobRole": "백엔드"}
    body["company"] = company_body()
    _set_text(body["company"], field, " \n\t")
    async with company_http() as (client, webhook, llm):
        response = await client.post(endpoint, json=body)
    assert response.status_code == 422
    assert webhook.payloads == []
    assert llm.calls == []


def test_text_is_trimmed_before_unicode_length_checks_and_core_is_immutable() -> None:
    body = company_body()
    limits = {"displayName": 30, "interviewerBrief": 600, "factId": 32, "title": 20, "content": 200, "signal": 100}
    for field, limit in limits.items():
        _set_text(body, field, f" \n{'😀' * limit}\t ")
    body.update(companyId="  3  ", category="  CONSUMER  ")
    core = CompanyContextRequest.model_validate(body).to_core()
    assert core.company_id == "3"
    assert core.category == "CONSUMER"
    assert core.display_name == "😀" * 30
    assert core.interviewer_brief == "😀" * 600
    assert core.facts[0].fact_id == "😀" * 32
    assert core.facts[0].title == "😀" * 20
    assert core.facts[0].content == "😀" * 200
    assert core.facts[2].positive_signals == ("😀" * 100,)
    with pytest.raises(ValidationError):
        core.__setattr__("display_name", "변경")
    with pytest.raises(ValidationError):
        core.facts[0].__setattr__("content", "변경")


@pytest.mark.parametrize(
    ("field", "limit"),
    [("displayName", 30), ("interviewerBrief", 600), ("factId", 32), ("title", 20), ("content", 200), ("signal", 100)],
)
def test_unicode_overflow_is_rejected_without_truncation(field: str, limit: int) -> None:
    body = company_body()
    _set_text(body, field, "😀" * (limit + 1))
    with pytest.raises(ValidationError):
        CompanyContextRequest.model_validate(body)


def test_duplicate_fact_ids_are_detected_after_trimming() -> None:
    body = company_body()
    body["facts"][1]["factId"] = " b1 "
    with pytest.raises(ValidationError, match="유일"):
        CompanyContextRequest.model_validate(body)


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("company", [{}, {"companyId": "3"}])
async def test_partial_company_objects_are_not_silently_ignored(endpoint: str, company: dict[str, Any]) -> None:
    body = tailor_body() if "tailor" in endpoint else feedback_body()
    if endpoint == "/questions/tailor":
        body["profile"] = {"jobRole": "백엔드"}
    body["company"] = company
    async with company_http() as (client, webhook, llm):
        response = await client.post(endpoint, json=body)
    assert response.status_code == 422
    assert webhook.payloads == llm.calls == []


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("explicit_null", [False, True])
async def test_missing_and_null_company_preserve_existing_interviews(endpoint: str, explicit_null: bool) -> None:
    body = tailor_body() if "tailor" in endpoint else feedback_body()
    if endpoint == "/questions/tailor":
        body["profile"] = {"jobRole": "백엔드"}
    if explicit_null:
        body["company"] = None
    async with company_http() as (client, webhook, llm):
        response = await client.post(endpoint, json=body)
    assert response.status_code == 202
    assert webhook.payloads[0]["status"] == "succeeded"
    assert all("<company_context>" not in call["messages"][0]["content"] for call in llm.calls)
    if "feedback" in endpoint:
        assert "companyFit" in webhook.payloads[0]["result"]
        assert webhook.payloads[0]["result"]["companyFit"] is None
