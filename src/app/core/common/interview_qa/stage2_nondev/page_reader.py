from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import Any

from pydantic import ValidationError

from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicTextClientError
from app.core.common.interview_qa.stage2_nondev.dto import (
    DocumentReading,
    EvidenceBlock,
    ExtractedDocument,
    ExtractedPage,
    PageReading,
    ReadRegion,
)
from app.core.common.interview_qa.stage2_nondev.masking import mask_text
from app.core.common.interview_qa.stage2_nondev.page_reader_prompt import SYSTEM
from app.core.common.interview_qa.stage2_nondev.ports import DocumentModelClient
from app.core.common.interview_qa.stage2_nondev.render import render_region
from app.core.common.tool_use import extract_tool_input

logger = logging.getLogger(__name__)
TOOL_NAME = "submit_page_reading"
TOOL: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": "원문 전사와 문서에서 관찰한 구조를 제출한다.",
    "strict": True,
    "input_schema": PageReading.model_json_schema(),
}
DEFAULT_BATCH_SIZE = 50


class PageReader:
    def __init__(
        self,
        client: DocumentModelClient,
        model: str = "claude-haiku-5-5",
        concurrency: int = 5,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_tokens: int = 4096,
        long_edge_px: int = 1568,
        reread_edge_px: int = 2400,
    ) -> None:
        if concurrency < 1 or batch_size < 1:
            raise ValueError("reader concurrency and batch size must be positive")
        self.client, self.model = client, model
        self.concurrency, self.batch_size, self.max_tokens = concurrency, batch_size, max_tokens
        self.long_edge_px, self.reread_edge_px = long_edge_px, reread_edge_px

    async def execute(self, documents: list[ExtractedDocument]) -> DocumentReading:
        reading = DocumentReading(documents)
        semaphore = asyncio.Semaphore(self.concurrency)
        plans = [
            (document, page, region) for document in documents for page in document.pages for region in page.regions
        ]
        started = time.monotonic()

        async def read_one(document: ExtractedDocument, page: ExtractedPage, region: ReadRegion) -> None:
            async with semaphore:
                await self._read_region(reading, document, page, region)

        for offset in range(0, len(plans), self.batch_size):
            await asyncio.gather(*(read_one(*plan) for plan in plans[offset : offset + self.batch_size]))
        reading.usage.elapsed_seconds = time.monotonic() - started
        order = {"TEXT": 0, "TABLE": 1, "VISION_TRANSCRIPT": 2, "VISION_RELATION": 3}
        for document in documents:
            for page in document.pages:
                page.blocks.sort(key=lambda block: (order[block.method], block.bbox[1], block.bbox[0], block.id))
        logger.info("nondev.page_reader.usage %s", reading.usage)
        return reading

    async def _read_region(
        self,
        reading: DocumentReading,
        document: ExtractedDocument,
        page: ExtractedPage,
        region: ReadRegion,
    ) -> None:
        result, needs_reread = await self._call(reading, document, page, region, self.long_edge_px, reread=False)
        if needs_reread:
            retried, _ = await self._call(reading, document, page, region, self.reread_edge_px, reread=True)
            rank = {"good": 2, "partial": 1, "low": 0}
            if retried is not None and (result is None or rank[retried.legibility] >= rank[result.legibility]):
                result = retried
            if retried is None or retried.legibility != "good":
                page.unread = True
        if result is None:
            page.unread = True
            return
        page.unread |= result.legibility != "good"
        path = f"{document.id}/p{page.number}"
        block_id = f"{path}/vision-{region.bbox}"
        transcript = mask_text(result.transcript).strip()
        if transcript:
            page.blocks.append(EvidenceBlock(block_id, path, region.bbox, "VISION_TRANSCRIPT", transcript))
        relations = [f"{relation.from_} → {relation.to}: {relation.label}" for relation in result.relations]
        if relations:
            page.blocks.append(
                EvidenceBlock(
                    block_id + "/relations", path, region.bbox, "VISION_RELATION", mask_text("\n".join(relations))
                )
            )
        if result.summary.strip():
            reading.summaries.setdefault(path, []).append(mask_text(result.summary))
        chart = result.chart.model_copy(
            update={
                "metric": _chart_value(result.chart.metric),
                "axes": _chart_value(result.chart.axes),
                "trend": _chart_value(result.chart.trend),
                "values": [value for item in result.chart.values if (value := _chart_value(item))],
            }
        )
        if any((chart.metric, chart.axes, chart.trend, chart.values)):
            page.blocks.append(
                EvidenceBlock(
                    block_id + "/chart", path, region.bbox, "VISION_RELATION", mask_text(chart.model_dump_json())
                )
            )

    async def _call(
        self,
        reading: DocumentReading,
        document: ExtractedDocument,
        page: ExtractedPage,
        region: ReadRegion,
        long_edge: int,
        *,
        reread: bool,
    ) -> tuple[PageReading | None, bool]:
        try:
            png = await asyncio.to_thread(render_region, document.pdf, region, long_edge)
            content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64.b64encode(png).decode("ascii"),
                    },
                }
            ]
            if region.tiled:
                overview = await asyncio.to_thread(render_region, document.pdf, ReadRegion(page.number, page.bbox), 512)
                content.insert(
                    0,
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64.b64encode(overview).decode("ascii"),
                        },
                    },
                )
            labels = (
                "" if page.kind == "F" else "\n".join(block.text for block in page.blocks if block.method == "TEXT")
            )
            context = f"{document.id}/p{page.number}, 전체: {page.bbox}, 지정 영역: {region.bbox}. 참고 라벨:\n{labels}"
            content.append({"type": "text", "text": context})
            if reread:
                reading.usage.reread_calls += 1
            else:
                reading.usage.initial_calls += 1
            response = await self.client.call(
                model=self.model,
                system=SYSTEM,
                messages=[{"role": "user", "content": content}],
                tools=[TOOL],
                tool_choice={"type": "tool", "name": TOOL_NAME},
                output_config={"effort": "low"},
                max_tokens=self.max_tokens,
            )
            reading.usage.input_tokens += response.input_tokens
            reading.usage.output_tokens += response.output_tokens
            reading.usage.sdk_retries += response.sdk_retries
            if response.stop_reason == "refusal":
                reading.usage.refusals += 1
                logger.info("nondev.page_reader.refusal category=%s", response.refusal_category)
                return None, False
            if response.stop_reason == "max_tokens":
                return None, True
            raw = extract_tool_input(response.content_blocks, TOOL_NAME)
            if raw is None:
                return None, True
            result = PageReading.model_validate(raw)
            return result, result.legibility != "good"
        except ValidationError:
            return None, True
        except (AnthropicTextClientError, ValueError, RuntimeError):
            # 호출 실패도 확대 재읽기를 1회 한다(document-analysis-v1 처리 3). 재읽기까지 실패하면 미판독이다.
            logger.warning("nondev.page_reader.unread document=%s page=%s reread=%s", document.id, page.number, reread)
            return None, True


def _chart_value(value: str) -> str:
    stripped = value.strip()
    return "" if stripped.casefold() in {"없음", "n/a"} else stripped
