from __future__ import annotations

import asyncio
import logging
import multiprocessing
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from typing import Any

from app.core.common.interview_qa.dto import ParsedPortfolio
from app.core.common.interview_qa.pdf_recovery_dto import Region
from app.core.common.interview_qa.ports.pdf_document_processor import PdfProcessingError, RenderedPage
from app.outbound.adapters.pdf_process_tasks import extract_document, render_page

logger = logging.getLogger(__name__)


class PymupdfDocumentProcessor:
    """One application-owned, bounded process pool. Cancellation never releases a running worker early."""

    def __init__(self, workers: int) -> None:
        self._workers = workers
        self._pool = self._new_pool()
        self._available = asyncio.Semaphore(workers)
        self._pending = 0
        self._restarted = False
        self._closed = False
        self._broken = False

    def _new_pool(self) -> ProcessPoolExecutor:
        return ProcessPoolExecutor(max_workers=self._workers, mp_context=multiprocessing.get_context("spawn"))

    async def extract(self, pdf_bytes: bytes) -> ParsedPortfolio:
        return await self._submit(extract_document, pdf_bytes)

    async def render(
        self,
        pdf_bytes: bytes,
        page_number: int,
        *,
        region: Region | None = None,
        dpi: int = 150,
        max_pixels: int = 12_000_000,
        long_edge_px: int = 1500,
    ) -> RenderedPage:
        return await self._submit(render_page, pdf_bytes, page_number, region, dpi, max_pixels, long_edge_px)

    async def _submit[T](self, operation: Callable[..., T], *arguments: Any) -> T:
        if self._closed or self._broken:
            raise PdfProcessingError("processor_broken")
        if self._pending >= self._workers * 2:
            raise PdfProcessingError("processor_busy")
        self._pending += 1
        acquired = False
        submitted = False
        pool = self._pool
        try:
            await self._available.acquire()
            acquired = True
            pool = self._pool
            future = asyncio.get_running_loop().run_in_executor(pool, operation, *arguments)
            submitted = True
            future.add_done_callback(self._release)
            return await asyncio.shield(future)
        except BrokenProcessPool as error:
            if pool is self._pool:
                self._reset_broken_pool()
            raise PdfProcessingError("processor_broken") from error
        finally:
            if not submitted:
                self._pending -= 1
                if acquired:
                    self._available.release()

    def _release(self, future: asyncio.Future[Any]) -> None:
        self._pending -= 1
        self._available.release()
        if not future.cancelled():
            future.exception()  # Consume late failures after a request cancellation.

    def _reset_broken_pool(self) -> None:
        if self._closed:
            return
        self._pool.shutdown(wait=False, cancel_futures=True)
        if self._restarted:
            self._broken = True
        else:
            self._restarted = True
            self._pool = self._new_pool()
        logger.error("pdf_process.pool_broken", extra={"restarted": self._restarted, "unavailable": self._broken})

    async def close(self) -> None:
        self._closed = True
        await asyncio.to_thread(self._pool.shutdown, wait=True, cancel_futures=True)
