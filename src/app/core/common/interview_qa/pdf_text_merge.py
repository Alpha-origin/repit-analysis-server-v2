from collections.abc import Sequence
from itertools import pairwise

from app.core.common.interview_qa.dto import PdfPage, TextBlock
from app.core.common.interview_qa.pdf_geometry import covered, intersection, overlap
from app.core.common.interview_qa.pdf_recovery_dto import Region
from app.core.common.interview_qa.stage2_text_quality import broken_characters, is_broken

_DUPLICATE_OVERLAP = 0.3
_CONFLICT_OVERLAP = 0.5


def merge_page_text(page: PdfPage, vision: Sequence[TextBlock]) -> None:
    """Preserve native originals; accept replacements only at matching locations."""
    native = page.raw_text_blocks or page.text_blocks
    globally_broken = is_broken("\n".join(block.text for block in native))
    good = [
        block for block in native if not (is_broken(block.text) or (globally_broken and broken_characters(block.text)))
    ]
    bad = [block for block in native if block not in good]
    accepted: list[TextBlock] = []
    for block in vision:
        same = [existing for existing in good + accepted if overlap(existing.bbox, block.bbox) >= _DUPLICATE_OVERLAP]
        if any(" ".join(existing.text.split()) == " ".join(block.text.split()) for existing in same):
            continue
        conflicts = [existing for existing in good if overlap(existing.bbox, block.bbox) >= _CONFLICT_OVERLAP]
        if conflicts:
            page.text_recovery.diagnostics.append("native_vision_conflict")
            page.text_recovery.unreadable_regions.extend(
                intersection(existing.bbox, block.bbox) for existing in conflicts
            )
            continue
        if any(overlap(existing.bbox, block.bbox) >= _CONFLICT_OVERLAP for existing in accepted):
            page.text_recovery.diagnostics.append("vision_region_conflict")
            page.text_recovery.unreadable_regions.append(block.bbox)
            continue
        accepted.append(block)
    for block in bad:
        if not covered(block.bbox, [replacement.bbox for replacement in accepted]):
            page.text_recovery.unreadable_regions.append(block.bbox)
    page.text_blocks = _order_blocks(page, good, accepted)
    sources = {block.source for block in page.text_blocks}
    page.text_source = "mixed" if len(sources) > 1 else ("vision" if sources == {"vision"} else "native")


def _order_blocks(page: PdfPage, good: list[TextBlock], accepted: list[TextBlock]) -> list[TextBlock]:
    if good:
        # Only vertically ordered insertions can safely be interleaved with native reading order.
        for block in accepted[:]:
            if any(
                overlap((0, block.bbox[1], page.page_width, block.bbox[3]), existing.bbox) > 0
                and overlap(block.bbox, existing.bbox) == 0
                for existing in good
            ):
                accepted.remove(block)
                page.text_recovery.unreadable_regions.append(block.bbox)
                page.text_recovery.diagnostics.append("ambiguous_reading_order")
        if accepted and any(left.bbox[1] > right.bbox[1] for left, right in pairwise(good)):
            page.text_recovery.diagnostics.append("ambiguous_reading_order")
            page.text_recovery.unreadable_regions.extend(block.bbox for block in accepted)
            accepted = []
        return sorted(good + accepted, key=lambda block: block.bbox[1]) if accepted else good
    return accepted  # Keep the model's reading order, including multi-column blocks.


def deduplicated_regions(regions: Sequence[Region]) -> list[Region]:
    return list(dict.fromkeys(regions))
