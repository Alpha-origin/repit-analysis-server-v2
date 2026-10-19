from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.core.common.applicant_profile.dto_v2 import ApplicantProfileV2, MaterialOut
from app.core.common.applicant_profile.prompt_v2 import system_prompt
from app.core.common.interview_qa.errors import PipelineError
from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicCallResult
from app.core.common.job_spec import DOC_TYPES, JOBS
from app.core.common.question_cycle.dto import QuestionCycleRequest
from app.core.common.question_cycle.generate import QuestionCycleGenerate
from app.inbound.http.applicant_profile.dto import ProfileRequest
from tests.document_analysis.test_profile_v2 import draft


def request_body() -> dict[str, Any]:
    return {
        "jobRole": "QA_ENGINEER",
        "documents": [{"type": "TEST_PLAN", "url": "https://example.com/a.pdf"}],
        "githubUrls": [],
        "callbackUrl": "https://example.com/callback",
    }


def test_registry_matches_nine_roles_nineteen_types_and_five_categories() -> None:
    assert {code for code, job in JOBS.items() if job.family != "GENERAL"} == {
        "FRONTEND",
        "BACKEND",
        "WEB_PUBLISHER",
        "DATA_ANALYST",
        "DBA_INFRA",
        "QA_ENGINEER",
        "SECURITY",
        "PRODUCT_MANAGER",
        "IT_CONSULTANT",
    }
    assert len(DOC_TYPES) == 20
    assert all(len(job.categories) == 5 and job.required_docs <= job.allowed_docs for job in JOBS.values())
    assert JOBS["QA_ENGINEER"].categories == (
        "test_design",
        "defect_reporting",
        "release_judgment",
        "automation",
        "quality_process",
    )


@pytest.mark.parametrize("role", [code for code, job in JOBS.items() if job.family == "NON_DEV"])
def test_nondev_prompts_include_category_descriptions_and_document_hints(role: str) -> None:
    job = JOBS[role]
    prompt = system_prompt(job)
    assert len(job.category_descriptions) == 5
    for code, label, description in zip(job.categories, job.category_labels, job.category_descriptions, strict=True):
        assert f"{code}: {label} — {description}" in prompt
    assert all(set(categories) <= set(job.categories) for _, categories in job.category_hints)
    assert all(document in job.allowed_docs or document == "SUPPLEMENT" for document, _ in job.category_hints)


@pytest.mark.parametrize("role", [code for code, job in JOBS.items() if job.family != "GENERAL"])
def test_valid_new_job_document_request(role: str) -> None:
    job = JOBS[role]
    body = request_body()
    body["jobRole"] = role
    body["documents"][0]["type"] = sorted(job.required_docs)[0]
    if job.github == "REQUIRED":
        body["githubUrls"] = ["https://github.com/o/r"]
    assert ProfileRequest.model_validate(body).job_role == role


@pytest.mark.parametrize(
    "change",
    [
        {"jobRole": "UNKNOWN"},
        {"documents": []},
        {"documents": [{"type": "PRD", "url": "https://example.com/a.pdf"}]},
        {"supplements": [{"title": "허용 안 됨"}]},
        {
            "jobRole": "PRODUCT_MANAGER",
            "documents": [{"type": "PRD", "url": "https://example.com/a.pdf"}],
            "githubUrls": ["https://github.com/o/r"],
        },
    ],
)
def test_invalid_material_combinations_rejected_before_background_job(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ProfileRequest.model_validate({**request_body(), **change})


def test_legacy_still_requires_portfolio_and_github() -> None:
    with pytest.raises(ValidationError):
        ProfileRequest.model_validate(
            {"portfolioUrl": "https://example.com/p.pdf", "callbackUrl": "https://example.com/cb"}
        )
    valid = ProfileRequest.model_validate(
        {
            "portfolioUrl": "https://example.com/p.pdf",
            "githubUrls": ["https://github.com/o/r"],
            "callbackUrl": "https://example.com/cb",
        }
    )
    assert valid.job_role is None


class CycleClient:
    def __init__(self, invalid: bool = False) -> None:
        self.invalid = invalid
        self.calls: list[dict[str, Any]] = []

    async def call(self, **kwargs: Any) -> AnthropicCallResult:
        self.calls.append(kwargs)
        questions = [
            {
                "set_no": number,
                "category": "invented" if self.invalid else category,
                "question": f"{number}번 {category}에서 어떤 결정을 했나요?",
                "intention": "판단 근거 확인",
                "expected_answer": "자료에 근거한 설명",
                "based_on": ["doc-1/p1"],
            }
            for number in (1, 2, 3)
            for category in ("test_design", "defect_reporting")
        ]
        return AnthropicCallResult(
            content_blocks=[
                {"type": "tool_use", "id": "fixed", "name": "submit_question_cycle", "input": {"questions": questions}}
            ],
            input_tokens=1,
            output_tokens=1,
        )


@pytest.mark.parametrize("invalid", [False, True])
async def test_cycle_consumes_stored_v2_material_without_pdf_and_rejects_unknown_category(invalid: bool) -> None:
    profile = ApplicantProfileV2(
        job_role="QA_ENGINEER",
        overview="QA 경험",
        sources=[],
        materials=[MaterialOut(**material.model_dump(), source_kind="DOCUMENT") for material in draft().materials],
    )
    client = CycleClient(invalid)
    generate = QuestionCycleGenerate(client, "stub", 1000, 1, 400, 30, 600)
    request = QuestionCycleRequest(profile=profile, mode="MULTI", callback_url="https://example.com/cb")
    if invalid:
        with pytest.raises(PipelineError):
            await generate.execute(request)
        assert len(client.calls) == 2
    else:
        questions = await generate.execute(request)
        assert len(questions) == 6
        assert [question.category for question in questions[:2]] == ["test_design", "defect_reporting"]
        assert "시니어 QA" in client.calls[0]["system"]
