from __future__ import annotations

import pytest

from app.core.common.interview_qa.stage2_nondev.dto import EvidenceBlock, ExtractedDocument, ExtractedPage, ReadRegion
from app.core.common.interview_qa.stage2_nondev.page_reader import PageReader
from tests.document_analysis.helpers import DocumentClient, page_output, pdf_bytes


def document(regions: int = 1) -> ExtractedDocument:
    page = ExtractedPage(
        1, "E", (0, 0, 600, 800), [], [], [ReadRegion(1, (0, i, 100, i + 100)) for i in range(regions)]
    )
    return ExtractedDocument("doc-1", "PRD", "정책", pdf_bytes(""), [page])


async def test_batch_size_does_not_truncate_fifty_two_units(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.core.common.interview_qa.stage2_nondev.page_reader.render_region", lambda *_: b"png")
    client = DocumentClient()
    result = await PageReader(client).execute([document(52)])
    assert len(client.calls) == result.usage.initial_calls == 52
    assert result.usage.sdk_retries == 52
    call = client.calls[0]
    assert call["output_config"] == {"effort": "low"}
    assert "temperature" not in call
    assert call["tool_choice"] == {"type": "tool", "name": "submit_page_reading"}
    assert call["messages"][0]["content"][0]["type"] == "image"


async def test_low_legibility_rereads_once() -> None:
    client = DocumentClient([page_output("low"), page_output()])
    result = await PageReader(client).execute([document()])
    assert result.usage.initial_calls == result.usage.reread_calls == 1
    assert not result.documents[0].pages[0].unread
    assert "결제 실패" in result.blocks[0].text


async def test_partial_transcript_survives_failed_reread() -> None:
    client = DocumentClient([page_output("partial"), "network"])
    result = await PageReader(client).execute([document()])
    assert result.documents[0].pages[0].unread
    assert result.blocks[0].text == "결제 실패 시 재시도는 최대 세 번 허용한다."


async def test_call_failure_recovers_with_zoomed_reread() -> None:
    client = DocumentClient(["network", page_output()])
    result = await PageReader(client).execute([document()])
    assert result.usage.reread_calls == 1
    assert not result.documents[0].pages[0].unread
    assert "결제 실패" in result.blocks[0].text


@pytest.mark.parametrize(("failure", "calls"), [("refusal", 1), ("network", 2), ("max_tokens", 2)])
async def test_failures_are_unread_without_fabricated_success(failure: str, calls: int) -> None:
    client = DocumentClient([failure] * 2)
    result = await PageReader(client).execute([document()])
    assert result.documents[0].pages[0].unread
    assert result.blocks == []
    assert len(client.calls) == calls


async def test_schema_error_rereads_and_relations_are_not_transcripts() -> None:
    output = page_output()
    output["relations"] = [{"from": "API", "to": "Redis", "label": "세션 저장"}]
    client = DocumentClient([{"kind": "diagram"}, output])
    result = await PageReader(client).execute([document()])
    assert result.usage.reread_calls == 1
    assert [block.method for block in result.blocks] == ["VISION_TRANSCRIPT", "VISION_RELATION"]


async def test_broken_text_is_not_passed_as_reference_labels() -> None:
    item = document()
    item.pages[0].kind = "F"
    item.pages[0].blocks = [EvidenceBlock("bad", "doc-1/p1", (0, 0, 1, 1), "TEXT", "깨진 라벨")]
    client = DocumentClient()
    await PageReader(client).execute([item])
    context = client.calls[0]["messages"][0]["content"][-1]["text"]
    assert "깨진 라벨" not in context


@pytest.mark.parametrize("sentinel", ["없음", "N/A", " n/a "])
async def test_absent_chart_sentinels_do_not_become_evidence(sentinel: str) -> None:
    output = page_output()
    output["chart"] = {"metric": sentinel, "axes": sentinel, "trend": sentinel, "values": [sentinel]}
    result = await PageReader(DocumentClient([output])).execute([document()])
    assert [block.method for block in result.blocks] == ["VISION_TRANSCRIPT"]


async def test_real_chart_values_survive_sentinel_normalization() -> None:
    output = page_output()
    output["chart"] = {"metric": "매출", "axes": "월", "trend": "없음", "values": ["N/A", "0", "20%"]}
    result = await PageReader(DocumentClient([output])).execute([document()])
    assert '"values":["0","20%"]' in result.blocks[-1].text
