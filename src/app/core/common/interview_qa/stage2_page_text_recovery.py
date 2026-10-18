from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from app.core.common.interview_qa.dto import ParsedPortfolio
from app.core.common.interview_qa.pdf_geometry import tiles
from app.core.common.interview_qa.pdf_page_transcription import PageTextTranscriber
from app.core.common.interview_qa.pdf_recovery_dto import RecoveryOptions, RecoveryPlan, Region
from app.core.common.interview_qa.pdf_recovery_work import RecoveryWork
from app.core.common.interview_qa.pdf_text_merge import merge_page_text
from app.core.common.interview_qa.ports.pdf_document_processor import PdfDocumentProcessor, PdfProcessingError

logger = logging.getLogger(__name__)


class Stage2PageTextRecovery:
    def __init__(
        self,
        processor: PdfDocumentProcessor,
        transcriber: PageTextTranscriber,
        options: RecoveryOptions,
    ) -> None:
        self._processor = processor
        self._transcriber = transcriber
        self._options = options

    async def execute(
        self,
        pdf_bytes: bytes,
        parsed: ParsedPortfolio,
        plans: list[RecoveryPlan],
    ) -> ParsedPortfolio:
        result, work = self._prepare(parsed, plans)
        if not self._options.enabled:
            return result
        slots = asyncio.Semaphore(self._options.concurrency)
        try:
            async with asyncio.timeout(self._options.total_timeout_seconds):
                await self._recover(pdf_bytes, work, slots)
        except TimeoutError:
            for item in work:
                if item.page.text_recovery.state != "no_text" and not item.completed:
                    item.problems.append((item.bounds, "timeout"))
                    if item.page.text_recovery.error_kind is None:
                        item.page.text_recovery.error_kind = "transient"
        for item in work:
            item.finish()
            logger.info(
                "pdf_text_recovery.page",
                extra={
                    "page_number": item.page.page_number + 1,
                    "status": item.page.text_recovery.state,
                    "reasons": item.page.text_recovery.reasons,
                    "call_count": item.page.text_recovery.call_count,
                },
            )
        return result

    def _prepare(
        self,
        parsed: ParsedPortfolio,
        plans: list[RecoveryPlan],
    ) -> tuple[ParsedPortfolio, list[RecoveryWork]]:
        result = parsed.model_copy(deep=True)
        by_number = {page.page_number: page for page in result.pages}
        for page in result.pages:
            page.text_recovery.evaluated = self._options.enabled
        selected = sorted(plans, key=lambda plan: (plan.priority, plan.page_number))[: self._options.max_pages]
        chosen = {plan.page_number for plan in selected}
        for plan in plans:
            recovery = by_number[plan.page_number].text_recovery
            recovery.reasons = plan.reasons
            if not self._options.enabled or plan.page_number not in chosen:
                recovery.state = "skipped"
                recovery.failure_reason = "skipped_budget" if self._options.enabled else "feature_disabled"
                if self._options.enabled:
                    merge_page_text(by_number[plan.page_number], [])
        work = [RecoveryWork(by_number[plan.page_number]) for plan in sorted(selected, key=lambda p: p.page_number)]
        return result, work

    async def _recover(self, pdf: bytes, work: list[RecoveryWork], slots: asyncio.Semaphore) -> None:
        # All selected pages receive a preview before calls are assigned in document order.
        await self._run_all([self._preview(pdf, item, slots) for item in work])
        available = self._options.max_calls
        initial = [item for item in work if item.page.text_recovery.state != "no_text" and not item.problems]
        admitted = []
        while initial and available:
            batch, initial = initial[:available], initial[available:]
            admitted.extend(batch)
            used = await self._run_all([self._call(pdf, item, item.bounds, slots, tile=False) for item in batch])
            available -= sum(used)
        for item in initial:
            item.problems.append((item.bounds, "skipped_budget"))
        zoom = [item for item in admitted if item.needs_zoom]
        tile_regions = {item.page.page_number: tiles(item.page.page_width, item.page.page_height) for item in zoom}
        for index in range(self._options.max_tiles_per_page):
            tile_batch: list[Coroutine[Any, Any, bool]] = []
            for item in zoom:
                region = tile_regions[item.page.page_number][index]
                if available <= 0:
                    item.problems.append((region, "skipped_budget"))
                    continue
                available -= 1
                tile_batch.append(self._call(pdf, item, region, slots, tile=True))
            used = await self._run_all(tile_batch)
            # Rendering failures do not consume logical API calls.
            available += sum(not called for called in used)
        for item in work:
            item.completed = True

    async def _preview(self, pdf: bytes, item: RecoveryWork, slots: asyncio.Semaphore) -> bool:
        async with slots:
            try:
                image = await self._processor.render(
                    pdf,
                    item.page.page_number,
                    dpi=36,
                    max_pixels=65_536,
                    long_edge_px=256,
                )
            except PdfProcessingError as error:
                self._render_failure(item, item.bounds, error)
                return False
            if image.definitely_blank:
                item.page.text_recovery.state = "no_text"
            return False

    async def _call(
        self,
        pdf: bytes,
        item: RecoveryWork,
        region: Region,
        slots: asyncio.Semaphore,
        *,
        tile: bool,
    ) -> bool:
        async with slots:
            try:
                image = await self._processor.render(
                    pdf,
                    item.page.page_number,
                    region=region,
                    dpi=self._options.render_dpi,
                    max_pixels=self._options.render_max_pixels,
                    long_edge_px=self._options.vision_long_edge_px,
                )
            except PdfProcessingError as error:
                self._render_failure(item, region, error)
                return False
            item.page.text_recovery.call_count += 1
            attempt = await self._transcriber.execute(image, item.page.page_number)
            item.accept(attempt, region, tile=tile)
            if not tile and not item.needs_zoom:
                item.completed = True
            return True

    @staticmethod
    def _render_failure(item: RecoveryWork, region: Region, error: PdfProcessingError) -> None:
        transient = error.kind in {"processor_busy", "processor_broken"}
        item.problems.append((region, "timeout" if transient else "render_error"))
        if transient:
            item.page.text_recovery.error_kind = "transient"

    @staticmethod
    async def _run_all(tasks: list[Coroutine[Any, Any, bool]]) -> list[bool]:
        # TaskGroup cancels and joins siblings on errors or request cancellation.
        async with asyncio.TaskGroup() as group:
            running = [group.create_task(task) for task in tasks]
        return [task.result() for task in running]
