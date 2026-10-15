from typing import Any

import pytest

from app.inbound.http.company_dto import CompanyContextRequest
from tests.company_helpers import company_body, company_http
from tests.multi_helpers import feedback_body, tailor_body

ENDPOINTS = ["/questions/tailor", "/questions/tailor/multi", "/feedback/solo", "/feedback/multi"]


def _invalid_company(case: str) -> dict[str, Any]:
    company = company_body()
    if case == "missing_title":
        del company["facts"][0]["title"]
    elif case == "duplicate_id":
        company["facts"][1]["factId"] = "b1"
    elif case in {"no_talent", "no_business"}:
        kind = "TALENT" if case == "no_talent" else "BUSINESS"
        company["facts"] = [fact for fact in company["facts"] if fact["kind"] != kind]
    elif case == "business_signals":
        company["facts"][0]["positiveSignals"] = ["잘못된 신호"]
    elif case == "too_many_facts":
        company["facts"] += [dict(company["facts"][0], factId=str(i)) for i in range(18)]
    else:
        _invalidate_fields(company, case)
    return company


def _invalidate_fields(company: dict[str, Any], case: str) -> None:
    if case in {"title", "content", "factId"}:
        limit = {"title": 20, "content": 200, "factId": 32}[case]
        company["facts"][0][case] = "가" * (limit + 1)
    elif case in {"positiveSignals", "concernSignals"}:
        company["facts"][2][case] = ["신호"] * 4
    elif case == "signal_length":
        company["facts"][2]["positiveSignals"] = ["가" * 101]
    elif case == "empty_signal":
        company["facts"][2]["concernSignals"] = [""]
    elif case == "empty_facts":
        company["facts"] = []
    elif case == "bad_kind":
        company["facts"][0]["kind"] = "INVALID"
    else:
        company[case] = "가" * ({"displayName": 30, "interviewerBrief": 600}[case] + 1)


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize(
    "case",
    [
        "missing_title",
        "duplicate_id",
        "no_talent",
        "no_business",
        "business_signals",
        "too_many_facts",
        "title",
        "content",
        "factId",
        "positiveSignals",
        "concernSignals",
        "signal_length",
        "empty_signal",
        "empty_facts",
        "bad_kind",
        "displayName",
        "interviewerBrief",
    ],
)
async def test_invalid_company_is_rejected_before_job_acceptance(endpoint: str, case: str) -> None:
    body = tailor_body() if "tailor" in endpoint else feedback_body()
    if endpoint == "/questions/tailor":
        body["profile"] = {"jobRole": "백엔드"}
    body["company"] = _invalid_company(case)
    async with company_http() as (client, webhook, llm):
        response = await client.post(endpoint, json=body)
    assert response.status_code == 422
    assert webhook.payloads == []
    assert llm.calls == []


@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_legacy_legal_size_is_ignored_and_company_is_accepted(endpoint: str) -> None:
    body = tailor_body() if "tailor" in endpoint else feedback_body()
    if endpoint == "/questions/tailor":
        body["profile"] = {"jobRole": "백엔드"}
    body["company"] = dict(company_body(), legalSize="LARGE")
    async with company_http() as (client, webhook, _):
        response = await client.post(endpoint, json=body)
    assert response.status_code == 202
    assert webhook.payloads[0]["status"] == "succeeded"


def test_boundary_lengths_and_unknown_category_are_accepted() -> None:
    body = company_body()
    body["displayName"] = "가" * 30
    body["interviewerBrief"] = "가" * 600
    body["category"] = "NEW_CATEGORY"
    body["facts"][0].update(title="가" * 20, content="가" * 200, factId="f" * 32)
    body["facts"][2]["positiveSignals"] = ["가" * 100] * 3
    body["facts"][2]["concernSignals"] = ["가" * 100] * 3
    core = CompanyContextRequest.model_validate(body).to_core()
    assert core.category == "NEW_CATEGORY"
    assert len(core.facts[2].positive_signals) == 3
    assert core.fact_by_id("unknown") is None
