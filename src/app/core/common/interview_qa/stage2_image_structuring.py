
from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass
from typing import Any

from app.core.common.interview_qa.dto import (
    ImageBlock,
    ImageStructure,
    PdfPage,
    StructuredPortfolio,
    TriagedPortfolio,
)
from app.core.common.interview_qa.image_context import surrounding_text
from app.core.common.interview_qa.image_resize import resize_for_vision
from app.core.common.interview_qa.ports.anthropic_text_client import (
    AnthropicTextClient,
    AnthropicTextClientError,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _StructuredImageResult:
    image: ImageStructure
    input_tokens: int
    output_tokens: int


# 비전 응답 강제용 tool 정의.
_STRUCTURING_TOOL: dict[str, Any] = {
    "name": "structure_image",
    "description": "이미지의 유형·요약·기술 신호를 분석해 제출합니다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "image_type": {
                "type": "string",
                "enum": [
                    "architecture_diagram",
                    "erd",
                    "ui_screenshot",
                    "flow_diagram",
                    "chart",
                    "other",
                ],
            },
            "summary": {
                "type": "string",
                "description": "이미지 내용을 1~2문장의 한국어로 요약.",
            },
            "tech_signals": {
                "type": "array",
                "description": "이미지에서 보이는 기술 키워드 짧은 리스트(예: 'Kafka', 'PostgreSQL').",
                "items": {"type": "string"},
            },
        },
        "required": ["image_type", "summary", "tech_signals"],
    },
}


_SYSTEM_PROMPT = (
    "너는 포트폴리오 PDF 안 이미지를 분석해 구조화된 정보를 제출하는 도우미다.\n"
    "유형은 아키텍처 다이어그램·ERD·UI 스크린샷·플로우 차트·차트·기타 중 하나로 분류한다.\n"
    "요약은 1~2문장의 한국어로, 면접 질문 작성에 도움 되는 핵심 정보를 담는다.\n"
    "tech_signals 는 이미지에서 관찰되는 기술 키워드의 짧은 리스트(없으면 빈 배열).\n"
    "반드시 structure_image 도구를 호출해 응답하라."
)


class Stage2ImageStructuring:


    def __init__(
        self,
        client: AnthropicTextClient,
        vision_model: str,
        max_tokens: int,
        concurrency: int,
        max_images_per_request: int,
        resize_long_edge_px: int,
        context_vertical_px: float,
        context_max_chars: int,
    ) -> None:
        self._client = client
        self._model = vision_model
        self._max_tokens = max_tokens
        self._concurrency = concurrency
        self._max_images = max_images_per_request
        self._resize_long_edge = resize_long_edge_px
        self._context_vertical_px = context_vertical_px
        self._context_max_chars = context_max_chars

    async def execute(self, triaged: TriagedPortfolio) -> StructuredPortfolio:

        if triaged.branch != "image_heavy":
            logger.info(
                "stage2_image_structuring.skipped",
                extra={"reason": triaged.branch},
            )
            return StructuredPortfolio(
                pages=triaged.pages,
                structured_images=[],
                branch=triaged.branch,
            )

        # (page, image_block) 쌍을 페이지 순서대로 모은다.
        candidates: list[tuple[PdfPage, ImageBlock]] = [
            (page, img) for page in triaged.pages for img in page.image_blocks
        ]
        input_total = len(candidates)

        # 한 요청당 비전 호출 총량 상한(비용 폭증 차단). 초과분은 그냥 드롭.
        dropped = 0
        if input_total > self._max_images:
            dropped = input_total - self._max_images
            candidates = candidates[: self._max_images]
            logger.warning(
                "stage2_image_structuring.over_limit",
                extra={
                    "input_total": input_total,
                    "cap": self._max_images,
                    "dropped": dropped,
                },
            )

        # 동시 호출 상한은 Semaphore 로.
        sem = asyncio.Semaphore(self._concurrency)
        tasks = [self._structure_one(sem, page, img) for page, img in candidates]
        results: list[BaseException | _StructuredImageResult | None] = await asyncio.gather(
            *tasks,
            return_exceptions=True,
        )

        structured: list[ImageStructure] = []
        failures = 0
        total_input_tokens = 0
        total_output_tokens = 0
        for r in results:
            if isinstance(r, _StructuredImageResult):
                structured.append(r.image)
                total_input_tokens += r.input_tokens
                total_output_tokens += r.output_tokens
            else:
                # None 또는 예외(return_exceptions=True 로 잡힘) — 한 장 실패.
                failures += 1

        logger.info(
            "stage2_image_structuring.token_usage input_tokens=%s output_tokens=%s total_tokens=%s attempted=%s "
            "succeeded=%s failed=%s",
            total_input_tokens,
            total_output_tokens,
            total_input_tokens + total_output_tokens,
            len(candidates),
            len(structured),
            failures,
            extra={
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "total_tokens": total_input_tokens + total_output_tokens,
                "attempted": len(candidates),
                "succeeded": len(structured),
                "failed": failures,
            },
        )
        logger.info(
            "stage2_image_structuring.done",
            extra={
                "input_total": input_total,
                "attempted": len(candidates),
                "succeeded": len(structured),
                "failed": failures,
                "dropped_over_limit": dropped,
            },
        )
        return StructuredPortfolio(
            pages=triaged.pages,
            structured_images=structured,
            branch=triaged.branch,
        )

    # ---------------- 한 장 처리 ----------------

    async def _structure_one(
        self,
        sem: asyncio.Semaphore,
        page: PdfPage,
        img: ImageBlock,
    ) -> _StructuredImageResult | None:

        async with sem:
            try:
                resized_bytes, media_type = await asyncio.to_thread(
                    resize_for_vision, img.image_bytes, self._resize_long_edge
                )
            except Exception:
                logger.warning(
                    "stage2_image_structuring.resize_failed",
                    extra={"page": page.page_number, "xref": img.xref},
                )
                return None

            context = surrounding_text(
                page,
                img,
                vertical_px=self._context_vertical_px,
                max_chars=self._context_max_chars,
            )

            user_content = self._build_user_content(resized_bytes, media_type, page.page_number, context)

            try:
                response = await self._client.call(
                    model=self._model,
                    system=_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_content}],
                    tools=[_STRUCTURING_TOOL],
                    tool_choice={"type": "tool", "name": "structure_image"},
                    max_tokens=self._max_tokens,
                )
            except AnthropicTextClientError as exc:
                logger.warning(
                    "stage2_image_structuring.api_error",
                    extra={"page": page.page_number, "xref": img.xref, "error": str(exc)},
                )
                return None

            parsed = _parse_structure(response.content_blocks, page.page_number)
            if parsed is None:
                return None
            return _StructuredImageResult(
                image=parsed,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
            )

    @staticmethod
    def _build_user_content(
        resized_bytes: bytes, media_type: str, page_number: int, context: str
    ) -> list[dict[str, Any]]:

        return [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.b64encode(resized_bytes).decode("ascii"),
                },
            },
            {
                "type": "text",
                "text": (
                    f"이 이미지는 PDF {page_number} 번 페이지에서 추출됐다.\n"
                    f"주변 텍스트:\n{context or '(주변 텍스트 없음)'}\n\n"
                    "structure_image 도구를 호출해 분석 결과를 제출하라."
                ),
            },
        ]


# ---------------- 응답 파싱 ----------------


def _parse_structure(
    content_blocks: list[dict[str, Any]],
    page_number: int,
) -> ImageStructure | None:

    for block in content_blocks:
        if block.get("type") != "tool_use" or block.get("name") != "structure_image":
            continue
        raw = block.get("input")
        # SDK 가 dict 로 주는 경우와, 드물게 문자열로 주는 경우를 모두 대비.
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                return None
        if not isinstance(raw, dict):
            return None

        image_type = raw.get("image_type")
        summary = raw.get("summary")
        tech = raw.get("tech_signals", [])
        if not isinstance(image_type, str) or not isinstance(summary, str):
            return None
        if not isinstance(tech, list):
            tech = []

        return ImageStructure(
            source_page=page_number,
            image_type=image_type,
            summary=summary,
            tech_signals=[s for s in tech if isinstance(s, str)],
        )
    return None
