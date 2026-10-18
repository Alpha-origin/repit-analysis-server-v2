from typing import cast

import pytest

from app.core.commands.dispatch_applicant_profile import _failure_code, _failure_payload
from app.core.common.interview_qa.dto import ImageStructure, ParsedPortfolio, StructuredPortfolio
from app.core.common.interview_qa.pdf_document_report import DocumentExtractionError, document_report
from app.core.common.interview_qa.pdf_recovery_dto import ApiErrorKind, RecoveryFailure, RecoveryState
from app.core.common.interview_qa.stage2_document_merge import Stage2DocumentMerge
from app.core.common.interview_qa.stage2_image_llm_triage import Stage2ImageLlmTriage
from app.core.common.interview_qa.stage2_image_triage import Stage2ImageTriage
from tests.pdf_recovery_helpers import page


@pytest.mark.parametrize(
    ("reason", "kind", "status", "code"),
    [
        ("api_error", "configuration", 500, "INTERNAL"),
        ("api_error", "unknown", 500, "INTERNAL"),
        ("invalid_response", None, 500, "INTERNAL"),
        ("timeout", "transient", 503, "DOCUMENT_EXTRACTION_UNAVAILABLE"),
        ("skipped_budget", None, 413, "DOCUMENT_EXTRACTION_LIMIT"),
        ("truncated", None, 413, "DOCUMENT_EXTRACTION_LIMIT"),
        ("render_error", None, 422, "DOCUMENT_UNREADABLE"),
        (None, None, 422, "DOCUMENT_UNREADABLE"),
    ],
)
async def test_empty_document_failure_and_callback_report(
    reason: str | None, kind: str | None, status: int, code: str
) -> None:
    target = page()
    recovery = target.text_recovery
    recovery.evaluated = True
    recovery.state = "failed" if reason else "no_text"
    recovery.failure_reason = cast("RecoveryFailure | None", reason)
    recovery.error_kind = cast("ApiErrorKind | None", kind)
    structured = StructuredPortfolio(pages=[target], structured_images=[], branch="text_heavy")
    with pytest.raises(DocumentExtractionError) as caught:
        await Stage2DocumentMerge().execute(structured)
    error = caught.value
    assert error.status_code == status
    assert _failure_code(error) == code
    payload = _failure_payload("job", status, error.message, error.code, error.report)
    assert payload["error"]["code"] == code
    assert payload["error"]["documentExtraction"]["pageCount"] == 1


async def test_disabled_empty_document_keeps_old_policy_and_not_evaluated() -> None:
    structured = StructuredPortfolio(pages=[page()], structured_images=[], branch="text_heavy")
    merged = await Stage2DocumentMerge().execute(structured)
    assert merged.portfolio_text == ""
    assert merged.document_extraction.status == "not_evaluated"


async def test_usable_image_allows_figure_only_document_and_display_number_one_based() -> None:
    target = page()
    target.text_recovery.evaluated = True
    target.text_recovery.state = "no_text"
    image = ImageStructure(source_page=0, image_type="erd", summary="주문과 상품 관계", tech_signals=["SQL"])
    merged = await Stage2DocumentMerge().execute(
        StructuredPortfolio(pages=[target], structured_images=[image], branch="image_heavy")
    )
    assert merged.document_extraction.no_text_pages == [1]
    assert merged.document_extraction.status == "complete"
    assert "p.1" in merged.portfolio_text
    assert "p.0" not in merged.portfolio_text
    assert "주문과 상품 관계" in merged.portfolio_text


async def test_partial_document_preserves_good_text_and_diagnostic_markers() -> None:
    good, failed = page(0, "Usable native"), page(1)
    for target in [good, failed]:
        target.text_recovery.evaluated = True
    failed.text_recovery.state = "failed"
    failed.text_recovery.failure_reason = "timeout"
    merged = await Stage2DocumentMerge().execute(
        StructuredPortfolio(pages=[good, failed], structured_images=[], branch="text_heavy")
    )
    report = merged.document_extraction.model_dump(by_alias=True)
    assert report["status"] == "partial"
    assert report["failedPages"] == [2]
    assert {"page": 2, "code": "timeout"} in report["warnings"]
    assert "Usable native" in merged.portfolio_text
    assert "미판독" in merged.portfolio_text


async def test_failure_precedence_and_report_page_lists() -> None:
    pages = [page(i) for i in range(4)]
    for target, state in zip(pages, ["recovered", "partial", "failed", "skipped"], strict=True):
        target.text_recovery.evaluated = True
        target.text_recovery.state = cast("RecoveryState", state)
    pages[1].text_recovery.failure_reason = "skipped_budget"
    pages[2].text_recovery.error_kind = "transient"
    pages[3].text_recovery.error_kind = "configuration"
    structured = StructuredPortfolio(pages=pages, structured_images=[], branch="text_heavy")
    report = document_report(structured)
    assert (report.recovered_pages, report.partial_pages, report.failed_pages, report.skipped_pages) == (
        [1],
        [2],
        [3],
        [4],
    )
    with pytest.raises(DocumentExtractionError) as caught:
        await Stage2DocumentMerge().execute(structured)
    assert caught.value.code == "INTERNAL"


async def test_image_triage_and_llm_filter_preserve_raw_and_recovery_metadata() -> None:
    target = page(text="Native")
    target.text_recovery.evaluated = True
    target.text_recovery.state = "partial"
    target.text_source = "mixed"
    triaged = await Stage2ImageTriage(
        min_px=10, max_aspect_ratio=10, min_area_ratio=0.01, info_img_threshold=3, repeated_min_ratio=0.5
    ).execute(ParsedPortfolio(pages=[target]))
    filtered, _ = Stage2ImageLlmTriage._filter_pages(triaged.pages, set())
    assert filtered[0].raw_text_blocks == target.raw_text_blocks
    assert filtered[0].text_source == "mixed"
    assert filtered[0].text_recovery == target.text_recovery
