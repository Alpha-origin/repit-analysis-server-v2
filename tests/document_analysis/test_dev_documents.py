from __future__ import annotations

from unittest.mock import AsyncMock, Mock

from app.core.common.applicant_profile.dto import ProfileJobRequest
from app.core.common.interview_qa.dto import ImageBlock, PdfPage, Stage1Result, TriagedPortfolio
from app.core.common.interview_qa.stage1_validation import Stage1Validation
from app.core.common.interview_qa.stage2_image_structuring import Stage2ImageStructuring
from app.core.common.interview_qa.stage2_nondev.dto import ProfileDocument
from tests.core.commands.test_dispatch_applicant_profile import _dispatcher
from tests.document_analysis.helpers import DocumentClient, pdf_bytes
from tests.profile_helpers import raw_profile


def request(count: int) -> ProfileJobRequest:
    return ProfileJobRequest(
        job_role="BACKEND",
        documents=tuple(ProfileDocument(type="PORTFOLIO", url=f"https://example.com/{i}.pdf") for i in range(count)),
        github_urls=("https://github.com/o/order-api",),
        callback_url="https://example.com/cb",
    )


async def test_developer_multiple_pdfs_reject_total_over_fifty_before_any_model_work() -> None:
    dispatcher, webhook = _dispatcher(raw_profile())
    data = pdf_bytes(*(["본문"] * 26))
    stage1 = Mock(spec=Stage1Validation)
    stage1.execute = AsyncMock(return_value=Stage1Result(pdf_bytes=data, repos=()))
    stage1._validate_pdf = AsyncMock(return_value=data)
    dispatcher._stage1 = stage1
    dispatcher._stage2_pdf = Mock(execute=AsyncMock())
    await dispatcher.execute("dev-limit", request(2))
    assert webhook.payloads[0]["error"]["code"] == "INVALID_MATERIAL"
    dispatcher._stage2_pdf.execute.assert_not_awaited()


async def test_developer_multiple_documents_share_existing_fifteen_image_budget() -> None:
    dispatcher, webhook = _dispatcher(raw_profile())
    data = pdf_bytes("본문")
    stage1 = Mock(spec=Stage1Validation)
    stage1.execute = AsyncMock(return_value=Stage1Result(pdf_bytes=data, repos=()))
    stage1._validate_pdf = AsyncMock(return_value=data)
    dispatcher._stage1 = stage1
    triaged = TriagedPortfolio(
        branch="image_heavy",
        info_img_count=12,
        pages=[
            PdfPage(
                page_number=0,
                page_width=600,
                page_height=800,
                text_blocks=[],
                image_blocks=[
                    ImageBlock(image_bytes=b"", width=100, height=100, bbox=(0, 0, 100, 100), xref=i) for i in range(12)
                ],
            )
        ],
    )
    dispatcher._stage2_llm_triage = Mock(execute=AsyncMock(return_value=triaged))
    structuring = Stage2ImageStructuring(DocumentClient(), "stub", 1000, 5, 15, 1568, 50, 300)
    attempts = AsyncMock(return_value=None)
    structuring._structure_one = attempts  # type: ignore[method-assign]
    dispatcher._stage2_structuring = structuring
    await dispatcher.execute("dev-images", request(3))
    assert webhook.payloads[0]["status"] == "succeeded"
    assert webhook.payloads[0]["result"]["profile"]["schemaVersion"] == 1
    assert attempts.await_count == 15
