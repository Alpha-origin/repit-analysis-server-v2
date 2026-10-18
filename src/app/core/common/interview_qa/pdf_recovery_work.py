from __future__ import annotations

from dataclasses import dataclass, field

from app.core.common.interview_qa.dto import PdfPage, TextBlock
from app.core.common.interview_qa.pdf_geometry import covered, from_normalized
from app.core.common.interview_qa.pdf_page_transcription import TranscriptionAttempt
from app.core.common.interview_qa.pdf_recovery_dto import RecoveryFailure, Region, UncertainText
from app.core.common.interview_qa.pdf_text_merge import deduplicated_regions, merge_page_text


@dataclass
class RecoveryWork:
    page: PdfPage
    vision: list[TextBlock] = field(default_factory=list)
    problems: list[tuple[Region, RecoveryFailure]] = field(default_factory=list)
    uncertain: list[Region] = field(default_factory=list)
    zoom_regions: list[Region] = field(default_factory=list)
    successful_tiles: list[Region] = field(default_factory=list)
    batches: list[tuple[Region, list[TextBlock]]] = field(default_factory=list)
    completed: bool = False
    had_success: bool = False
    no_text: bool = False

    @property
    def bounds(self) -> Region:
        return 0, 0, self.page.page_width, self.page.page_height

    @property
    def needs_zoom(self) -> bool:
        return bool(self.uncertain or self.zoom_regions or any(reason == "truncated" for _, reason in self.problems))

    def accept(self, attempt: TranscriptionAttempt, region: Region, *, tile: bool) -> None:
        recovery = self.page.text_recovery
        recovery.input_tokens += attempt.input_tokens
        recovery.output_tokens += attempt.output_tokens
        if attempt.failure:
            self.problems.append((region, attempt.failure))
            priority = {None: -1, "transient": 0, "unknown": 1, "configuration": 2}
            if priority[attempt.error_kind] > priority[recovery.error_kind]:
                recovery.error_kind = attempt.error_kind
        result = attempt.result
        if result is None:
            return
        self.had_success = True
        self.no_text = self.no_text or (not tile and result.status == "no_text")
        unclear = [from_normalized(box, region) for box in result.unreadable_regions]
        blocks = []
        for block in result.blocks:
            box = from_normalized(block.bbox_normalized, region)
            if block.legibility == "clear":
                blocks.append(TextBlock(text=block.text, bbox=box, source="vision", source_region=region))
            else:
                unclear.append(box)
                recovery.uncertain_blocks.append(UncertainText(text=block.text, bbox=box, source_region=region))
        self.batches.append((region, blocks))
        if result.status == "unreadable":
            unclear.append(region)
        self.uncertain.extend(unclear)
        if result.needs_zoom:
            self.zoom_regions.append(region)
        if tile and not attempt.failure and not unclear and not result.needs_zoom:
            self.successful_tiles.append(region)

    def finish(self) -> None:
        recovery = self.page.text_recovery
        # Full-page reading order first, followed by deterministic column/strip tile order.
        ordered = sorted(self.batches, key=lambda batch: (batch[0] != self.bounds, batch[0][0], batch[0][1]))
        self.vision = [block for _, blocks in ordered for block in blocks]
        remaining = [(box, reason) for box, reason in self.problems if not covered(box, self.successful_tiles)]
        recovery.unreadable_regions = [box for box in self.uncertain if not covered(box, self.successful_tiles)]
        recovery.unreadable_regions.extend(box for box in self.zoom_regions if not covered(box, self.successful_tiles))
        recovery.unreadable_regions.extend(box for box, _ in remaining)
        merge_page_text(self.page, self.vision)
        recovery.unreadable_regions = deduplicated_regions(recovery.unreadable_regions)
        if self.no_text and self.page.text_blocks:
            recovery.diagnostics.append("native_vision_conflict")
        partial = bool(recovery.unreadable_regions or recovery.diagnostics or remaining)
        recovery.failure_reason = next((reason for _, reason in remaining), None)
        if self.had_success:
            recovery.state = "partial" if partial else ("recovered" if self.vision else "no_text")
        elif remaining:
            recovery.state = "skipped" if all(reason == "skipped_budget" for _, reason in remaining) else "failed"
        elif recovery.state != "no_text":
            recovery.state = "failed"
