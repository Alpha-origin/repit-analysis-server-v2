from __future__ import annotations

from unittest.mock import Mock

import pytest
from dishka import Provider, Scope, make_async_container
from dishka.integrations.fastapi import setup_dishka
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.commands.dispatch_applicant_profile import DispatchApplicantProfile
from app.core.common.applicant_profile.exploration import ProfileExploration
from app.core.common.applicant_profile.refine import ProfileEvidenceRefine
from app.core.common.interview_qa.stage1_validation import Stage1Validation
from app.core.common.interview_qa.stage2_document_merge import Stage2DocumentMerge
from app.core.common.interview_qa.stage2_image_llm_triage import Stage2ImageLlmTriage
from app.core.common.interview_qa.stage2_image_structuring import Stage2ImageStructuring
from app.core.common.interview_qa.stage2_image_triage import Stage2ImageTriage
from app.core.common.interview_qa.stage2_page_text_recovery import Stage2PageTextRecovery
from app.core.common.interview_qa.stage2_pdf_extract import Stage2PdfExtract
from app.core.common.interview_qa.stage2_text_clean import Stage2TextClean
from app.core.common.interview_qa.stage2_text_quality import Stage2TextQuality
from app.core.common.interview_qa.stage3_repo_tree import Stage3RepoTree
from app.inbound.http.applicant_profile.router import make_applicant_profile_router
from tests.document_analysis.helpers import DocumentClient, pdf_bytes
from tests.document_analysis.test_profile_v2 import BODY, builder, draft
from tests.document_analysis.test_request_cycle import request_body
from tests.multi_helpers import RecordingWebhook


@pytest.mark.parametrize("unreadable", [False, True])
async def test_profile_http_accepts_new_contract_and_delivers_camelcase_v2_or_specific_failure(
    unreadable: bool,
) -> None:
    llm = DocumentClient(profile=draft().model_dump())
    webhook = RecordingWebhook()
    dispatcher = DispatchApplicantProfile(
        webhook,
        Mock(spec=Stage1Validation),
        Mock(spec=Stage2PdfExtract),
        Mock(spec=Stage2ImageTriage),
        Mock(spec=Stage2ImageLlmTriage),
        Mock(spec=Stage2ImageStructuring),
        Mock(spec=Stage2DocumentMerge),
        Mock(spec=Stage3RepoTree),
        Mock(spec=ProfileExploration),
        ProfileEvidenceRefine(),
        Mock(spec=Stage2PageTextRecovery),
        Mock(spec=Stage2TextQuality),
        Mock(spec=Stage2TextClean),
        builder(llm, pdf_bytes("" if unreadable else BODY)),
    )
    provider = Provider()
    provider.provide(lambda: dispatcher, provides=DispatchApplicantProfile, scope=Scope.APP)
    container = make_async_container(provider)
    app = FastAPI()
    setup_dishka(container, app)
    app.include_router(make_applicant_profile_router())
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/profile", json=request_body())
            assert response.status_code == 202
            (payload,) = webhook.payloads
            assert payload["jobId"] == response.json()["jobId"]
            if unreadable:
                assert payload["error"]["code"] == "UNREADABLE_DOCUMENT"
                assert llm.calls == []
            else:
                result = payload["result"]
                assert result["profile"]["schemaVersion"] == 2
                assert result["profile"]["jobRole"] == "QA_ENGINEER"
                assert result["profile"]["materials"][0]["sourceKind"] == "DOCUMENT"
                assert result["projectSummary"]["coreFeatures"][0]["basedOn"] == ["doc-1/p1"]
    finally:
        await container.close()
