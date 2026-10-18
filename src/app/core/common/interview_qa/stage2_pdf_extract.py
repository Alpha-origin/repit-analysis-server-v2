from app.core.common.interview_qa.dto import ParsedPortfolio
from app.core.common.interview_qa.ports.pdf_document_processor import PdfDocumentProcessor


class Stage2PdfExtract:
    def __init__(self, processor: PdfDocumentProcessor) -> None:
        self._processor = processor

    async def execute(self, pdf_bytes: bytes) -> ParsedPortfolio:
        return await self._processor.extract(pdf_bytes)
