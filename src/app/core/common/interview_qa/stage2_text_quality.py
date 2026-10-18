import unicodedata

from app.core.common.interview_qa.dto import ParsedPortfolio
from app.core.common.interview_qa.pdf_geometry import area, intersection, union_area
from app.core.common.interview_qa.pdf_recovery_dto import RecoveryPlan

_MIN_BROKEN = 5
_BROKEN_RATIO = 0.02
_SHORT_CHARS = 50
_PARTIAL_CHARS = 200
_IMAGE_COVERAGE = 0.6


def broken_characters(text: str) -> int:
    return sum(
        char == "\ufffd" or (unicodedata.category(char) == "Cc" and char not in "\t\n\r\f\v\u0085") for char in text
    )


def is_broken(text: str) -> bool:
    bad = broken_characters(text)
    return bad >= _MIN_BROKEN and bad / max(1, len(text)) >= _BROKEN_RATIO


class Stage2TextQuality:
    async def execute(self, portfolio: ParsedPortfolio) -> list[RecoveryPlan]:
        plans = []
        for page in portfolio.pages:
            text = "\n".join(block.text for block in page.raw_text_blocks or page.text_blocks)
            count = len("".join(text.split()))
            reasons = []
            if page.native_extraction_error:
                reasons.append("native_extraction_error")
            if count == 0:
                reasons.append("empty_text")
            if is_broken(text):
                reasons.append("broken_characters")
            if 0 < count < _SHORT_CHARS:
                reasons.append("short_text")
            bounds = (0.0, 0.0, page.page_width, page.page_height)
            image_area = union_area([intersection(image.bbox, bounds) for image in page.image_blocks])
            if count < _PARTIAL_CHARS and area(bounds) and image_area / area(bounds) >= _IMAGE_COVERAGE:
                reasons.append("partial_extraction")
            if reasons:
                urgent = any(
                    reason in reasons for reason in ("native_extraction_error", "empty_text", "broken_characters")
                )
                plans.append(RecoveryPlan(page_number=page.page_number, reasons=reasons, priority=0 if urgent else 1))
        return plans
