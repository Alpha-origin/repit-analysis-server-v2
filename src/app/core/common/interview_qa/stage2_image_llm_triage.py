"""Stage 2-3 — image_heavy 분기 전용 2차 LLM 트리아지.

1차(규칙) 트리아지를 통과한 이미지들 중 애매한 것을 Claude 텍스트 모델이
"정보성 vs 장식" 으로 정밀 판별해 한 번 더 거른다. 비전 호출은 다음 단계에서
하므로 여기서는 이미지의 픽셀 데이터를 보내지 않고, 메타데이터(크기·위치) +
주변 텍스트만으로 판단한다.

``branch == "text_heavy"`` 인 경우 호출 자체를 건너뛴다.

판단 실패 / LLM 응답 이상 시: **모두 정보성으로 간주**한다. 즉 LLM 트리아지는
"확실히 장식인 것만 추가로 빼는" 한 방향 필터로 동작한다. 안전 우선.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.core.common.interview_qa.dto import (
    PdfPage,
    TriagedPortfolio,
)
from app.core.common.interview_qa.image_context import surrounding_text
from app.core.common.interview_qa.ports.anthropic_text_client import (
    AnthropicTextClient,
    AnthropicTextClientError,
)

logger = logging.getLogger(__name__)


# 도구 정의 — Claude 가 반드시 이 형태로 응답하도록 tool_choice 로 강제한다.
_TRIAGE_TOOL: dict[str, Any] = {
    "name": "triage_judgment",
    "description": "각 이미지가 면접 질문 생성에 도움 되는 정보성인지, 단순 장식인지 판별한 결과를 제출합니다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "judgments": {
                "type": "array",
                "description": "전달받은 image_id 각각에 대한 판별 결과.",
                "items": {
                    "type": "object",
                    "properties": {
                        "image_id": {"type": "string"},
                        "verdict": {
                            "type": "string",
                            "enum": ["informative", "decorative"],
                        },
                        "reason": {
                            "type": "string",
                            "description": "한 줄짜리 판단 근거(선택).",
                        },
                    },
                    "required": ["image_id", "verdict"],
                },
            },
        },
        "required": ["judgments"],
    },
}


_SYSTEM_PROMPT = (
    "너는 포트폴리오 PDF 안의 이미지가 '정보성'인지 '장식'인지 판별하는 도우미다.\n"
    "정보성: 시스템 구조도, ERD, UI 스크린샷, 플로우 차트, 데이터 표, 아키텍처 다이어그램 등 "
    "면접 질문 생성에 도움이 되는 것.\n"
    "장식: 로고, 배경 일러스트, 구분선, 아이콘 등 정보가 거의 없는 것.\n"
    "각 이미지에 대해 image_id 와 함께 verdict 를 반환하라. 애매하면 informative 로 답하라"
    "(과도하게 제거하지 마라). 반드시 triage_judgment 도구를 호출해 응답하라."
)


class Stage2ImageLlmTriage:
    """LLM 기반 2차 이미지 트리아지."""

    def __init__(
        self,
        client: AnthropicTextClient,
        text_model: str,
        max_tokens: int,
        context_vertical_px: float,
        context_max_chars: int,
    ) -> None:
        self._client = client
        self._model = text_model
        self._max_tokens = max_tokens
        self._context_vertical_px = context_vertical_px
        self._context_max_chars = context_max_chars

    async def execute(self, triaged: TriagedPortfolio) -> TriagedPortfolio:
        """``branch == "image_heavy"`` 면 LLM 호출, 아니면 그대로 통과."""
        if triaged.branch != "image_heavy":
            logger.info(
                "stage2_image_llm_triage.skipped",
                extra={"reason": "text_heavy"},
            )
            return triaged

        candidates = self._collect_candidates(triaged.pages)
        if not candidates:
            return triaged

        verdicts = await self._ask_llm(candidates)

        # verdict 가 없거나 응답이 비정상이면 그 image_id 는 informative 로 본다(안전).
        # verdict 가 명확히 decorative 인 것만 추가로 제거.
        decorative_ids: set[str] = {
            cand["image_id"] for cand in candidates if verdicts.get(cand["image_id"]) == "decorative"
        }

        new_pages, info_count = self._filter_pages(triaged.pages, decorative_ids)

        logger.info(
            "stage2_image_llm_triage.done",
            extra={
                "input_images": len(candidates),
                "info_images": info_count,
                "removed_by_llm": len(decorative_ids),
            },
        )
        return TriagedPortfolio(
            pages=new_pages,
            branch=triaged.branch,
            info_img_count=info_count,
        )

    # ---------------- 후보 수집 + 컨텍스트 ----------------

    def _collect_candidates(self, pages: list[PdfPage]) -> list[dict[str, Any]]:
        """각 이미지에 대해 LLM 에 보낼 메타 + 주변 텍스트를 모은다."""
        candidates: list[dict[str, Any]] = []
        for page in pages:
            for img in page.image_blocks:
                ctx = surrounding_text(
                    page,
                    img,
                    vertical_px=self._context_vertical_px,
                    max_chars=self._context_max_chars,
                )
                candidates.append(
                    {
                        "image_id": _image_id(page.page_number, img.xref),
                        "page_number": page.page_number,
                        "page_size": (page.page_width, page.page_height),
                        "img_pixels": (img.width, img.height),
                        "img_bbox": img.bbox,
                        "surrounding": ctx,
                    }
                )
        return candidates

    # ---------------- LLM 호출 + 응답 파싱 ----------------

    async def _ask_llm(self, candidates: list[dict[str, Any]]) -> dict[str, str]:
        """LLM 에 한 번 호출해 image_id → verdict 매핑을 만든다.

        호출/파싱이 실패하면 빈 dict 를 돌려준다 → 모든 이미지가 informative 처리됨.
        """
        user_text = _build_user_prompt(candidates)
        try:
            response = await self._client.call(
                model=self._model,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_text}],
                tools=[_TRIAGE_TOOL],
                tool_choice={"type": "tool", "name": "triage_judgment"},
                max_tokens=self._max_tokens,
            )
        except AnthropicTextClientError as exc:
            logger.warning("stage2_image_llm_triage.api_error", extra={"error": str(exc)})
            return {}

        return _parse_judgments(response.content_blocks)

    @staticmethod
    def _filter_pages(pages: list[PdfPage], decorative_ids: set[str]) -> tuple[list[PdfPage], int]:
        """LLM 이 장식이라고 판단한 image_id 를 페이지별로 제거."""
        new_pages: list[PdfPage] = []
        info_count = 0
        for page in pages:
            kept = [img for img in page.image_blocks if _image_id(page.page_number, img.xref) not in decorative_ids]
            info_count += len(kept)
            new_pages.append(
                PdfPage(
                    page_number=page.page_number,
                    page_width=page.page_width,
                    page_height=page.page_height,
                    text_blocks=page.text_blocks,
                    image_blocks=kept,
                )
            )
        return new_pages, info_count


# ---------------- 모듈 헬퍼 ----------------


def _image_id(page_number: int, xref: int) -> str:
    """프롬프트와 응답에서 사용할 이미지 식별자."""
    return f"p{page_number}_x{xref}"


def _build_user_prompt(candidates: list[dict[str, Any]]) -> str:
    """이미지 후보 목록을 사람이 읽기 좋은 형식의 한국어 프롬프트로 만든다."""
    lines: list[str] = [
        "다음 이미지들이 정보성인지 장식인지 판별해 주세요. 각 이미지 블록을 보고 triage_judgment 도구를 호출하세요.",
        "",
    ]
    for i, cand in enumerate(candidates, start=1):
        page_w, page_h = cand["page_size"]
        img_w, img_h = cand["img_pixels"]
        x0, y0, x1, y1 = cand["img_bbox"]
        bbox_area = max(0.0, (x1 - x0) * (y1 - y0))
        page_area = page_w * page_h
        area_pct = (bbox_area / page_area * 100.0) if page_area > 0 else 0.0
        lines.append(f"[이미지 {i}]")
        lines.append(f"image_id: {cand['image_id']}")
        lines.append(f"페이지: {cand['page_number']}")
        lines.append(f"이미지 픽셀 크기: {img_w}x{img_h}")
        lines.append(f"페이지 내 위치(bbox): ({x0:.0f}, {y0:.0f}, {x1:.0f}, {y1:.0f}), 페이지 면적의 {area_pct:.1f}%")
        lines.append("주변 텍스트:")
        lines.append(cand["surrounding"] or "(주변 텍스트 없음)")
        lines.append("")
    return "\n".join(lines)


def _parse_judgments(content_blocks: list[dict[str, Any]]) -> dict[str, str]:
    """응답 블록 중 ``tool_use`` 호출의 input.judgments 를 dict 로 변환.

    응답이 비정상이면 빈 dict 를 돌려준다(호출 측이 informative 로 처리).
    """
    for block in content_blocks:
        if block.get("type") != "tool_use" or block.get("name") != "triage_judgment":
            continue
        raw_input = block.get("input")
        # SDK 가 dict 로 주는 경우와, 드물게 문자열로 주는 경우를 모두 대비.
        if isinstance(raw_input, str):
            try:
                raw_input = json.loads(raw_input)
            except json.JSONDecodeError:
                return {}
        if not isinstance(raw_input, dict):
            return {}
        judgments = raw_input.get("judgments")
        if not isinstance(judgments, list):
            return {}
        result: dict[str, str] = {}
        for entry in judgments:
            if not isinstance(entry, dict):
                continue
            image_id = entry.get("image_id")
            verdict = entry.get("verdict")
            if isinstance(image_id, str) and verdict in ("informative", "decorative"):
                result[image_id] = verdict
        return result
    return {}
