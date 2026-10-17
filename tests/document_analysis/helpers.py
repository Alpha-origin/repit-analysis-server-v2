from __future__ import annotations

import copy
import io
from typing import Any

import fitz
from PIL import Image

from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicCallResult, AnthropicTextClientError


def pdf_bytes(*texts: str, visual: bool = False, size: tuple[int, int] = (600, 800)) -> bytes:
    with fitz.open() as document:
        for text in texts:
            page = document.new_page(width=size[0], height=size[1])
            if text:
                page.insert_textbox(fitz.Rect(40, 180, size[0] - 40, size[1] - 40), text, fontsize=10, fontname="korea")
            if visual:
                page.draw_rect(fitz.Rect(60, 60, 120, 110))
                page.draw_rect(fitz.Rect(180, 60, 240, 110))
                page.draw_line(fitz.Point(120, 85), fitz.Point(180, 85))
        return bytes(document.tobytes())


def image_pdf() -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (400, 600), "white").save(stream, format="PNG")
    with fitz.open() as document:
        page = document.new_page(width=400, height=600)
        page.insert_image(page.rect, stream=stream.getvalue())
        return bytes(document.tobytes())


def page_output(
    legibility: str = "good", transcript: str = "결제 실패 시 재시도는 최대 세 번 허용한다."
) -> dict[str, Any]:
    return {
        "kind": "diagram",
        "transcript": transcript,
        "relations": [],
        "chart": {"metric": "", "axes": "", "trend": "", "values": []},
        "summary": "결제 재시도 정책",
        "legibility": legibility,
    }


class DocumentClient:
    def __init__(
        self, outputs: list[dict[str, Any] | str] | None = None, profile: dict[str, Any] | None = None
    ) -> None:
        self.outputs = list(outputs or [])
        self.profile = profile
        self.calls: list[dict[str, Any]] = []
        self.count_calls: list[dict[str, Any]] = []
        self.token_limit_by_length = False

    async def call(self, **kwargs: Any) -> AnthropicCallResult:
        self.calls.append(copy.deepcopy(kwargs))
        name = kwargs["tools"][-1]["name"]
        output = (
            self.outputs.pop(0)
            if name == "submit_page_reading" and self.outputs
            else (self.profile if name == "submit_profile_v2" else page_output())
        )
        if output == "network":
            raise AnthropicTextClientError("mock network error")
        stop = output if isinstance(output, str) else "tool_use"
        blocks = (
            [{"type": "tool_use", "id": "fixed", "name": name, "input": output}] if isinstance(output, dict) else []
        )
        return AnthropicCallResult(
            content_blocks=blocks, input_tokens=10, output_tokens=5, stop_reason=stop, sdk_retries=1
        )

    async def count_tokens(self, **kwargs: Any) -> int:
        self.count_calls.append(copy.deepcopy(kwargs))
        return len(kwargs["messages"][0]["content"]) if self.token_limit_by_length else 100


class Fetcher:
    def __init__(self, data: bytes) -> None:
        self.data = data

    async def fetch(self, url: str) -> bytes:
        return self.data
