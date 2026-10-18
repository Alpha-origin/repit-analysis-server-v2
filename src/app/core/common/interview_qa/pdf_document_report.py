from typing import Literal

from app.core.common.interview_qa.dto import StructuredPortfolio
from app.core.common.interview_qa.errors import PipelineError
from app.core.common.interview_qa.pdf_recovery_dto import DocumentExtractionReport, ExtractionWarning

DocumentFailureCode = Literal[
    "INTERNAL",
    "DOCUMENT_EXTRACTION_UNAVAILABLE",
    "DOCUMENT_EXTRACTION_LIMIT",
    "DOCUMENT_UNREADABLE",
]


class DocumentExtractionError(PipelineError):
    def __init__(self, status: int, code: DocumentFailureCode, report: DocumentExtractionReport) -> None:
        super().__init__(status, "PDF 내용을 충분히 읽을 수 없습니다.")
        self.code = code
        self.report = report


def document_report(structured: StructuredPortfolio) -> DocumentExtractionReport:
    report = DocumentExtractionReport(page_count=len(structured.pages))
    evaluated = any(page.text_recovery.evaluated for page in structured.pages)
    report.status = "complete" if evaluated else "not_evaluated"
    groups = {
        "recovered": report.recovered_pages,
        "partial": report.partial_pages,
        "failed": report.failed_pages,
        "skipped": report.skipped_pages,
        "no_text": report.no_text_pages,
    }
    for page in structured.pages:
        recovery = page.text_recovery
        number = page.page_number + 1
        if recovery.state in groups:
            groups[recovery.state].append(number)
        codes = [*recovery.reasons, *recovery.diagnostics]
        if recovery.failure_reason:
            codes.append(recovery.failure_reason)
        if recovery.unreadable_regions:
            codes.append("unreadable_regions")
        report.warnings.extend(ExtractionWarning(page=number, code=code) for code in dict.fromkeys(codes))
        if evaluated and (recovery.state in {"partial", "failed", "skipped"} or recovery.diagnostics):
            report.status = "partial"
    return report


def require_document_content(structured: StructuredPortfolio, report: DocumentExtractionReport) -> None:
    if report.status == "not_evaluated":
        return
    has_text = any(block.text.strip() for page in structured.pages for block in page.text_blocks)
    has_images = any(image.summary.strip() or any(image.tech_signals) for image in structured.structured_images)
    if has_text or has_images:
        return
    recoveries = [page.text_recovery for page in structured.pages]
    if any(
        item.error_kind in {"configuration", "unknown"} or item.failure_reason == "invalid_response"
        for item in recoveries
    ):
        raise DocumentExtractionError(500, "INTERNAL", report)
    if any(item.error_kind == "transient" or item.failure_reason == "timeout" for item in recoveries):
        raise DocumentExtractionError(503, "DOCUMENT_EXTRACTION_UNAVAILABLE", report)
    if any(item.failure_reason in {"skipped_budget", "truncated"} for item in recoveries):
        raise DocumentExtractionError(413, "DOCUMENT_EXTRACTION_LIMIT", report)
    raise DocumentExtractionError(422, "DOCUMENT_UNREADABLE", report)
