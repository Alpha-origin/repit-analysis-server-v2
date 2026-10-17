from __future__ import annotations

import io
import itertools

import fitz
import pytest
from PIL import Image

from app.core.common.interview_qa.errors import PipelineError
from app.core.common.interview_qa.stage2_nondev.classify import classify_page, plan_regions
from app.core.common.interview_qa.stage2_nondev.dto import (
    DocumentReading,
    EvidenceBlock,
    ExtractedDocument,
    ExtractedPage,
    ProfileDocument,
    TableData,
)
from app.core.common.interview_qa.stage2_nondev.extract import DocumentExtract
from app.core.common.interview_qa.stage2_nondev.masking import mask_text
from app.core.common.interview_qa.stage2_nondev.merge import page_text
from app.core.common.interview_qa.stage2_nondev.render import render_region
from app.core.common.interview_qa.stage2_nondev.tables import mark_continuations, markdown_table, select_rows
from app.core.common.interview_qa.stage2_pdf_extract import Stage2PdfExtract
from tests.document_analysis.helpers import image_pdf, pdf_bytes


@pytest.mark.parametrize(
    ("text", "area", "drawings", "visual", "kind"),
    [
        ("가" * 220, 0, 0, False, "A"),
        ("가" * 220, 0.01, 0, True, "B"),
        ("", 0, 0, False, "C"),
        ("", 1, 0, True, "D"),
        ("시작 종료", 0, 3, True, "E"),
        ("(cid:15)" * 50, 0, 0, False, "F"),
        ("\ufffd" * 100, 0, 0, False, "F"),
    ],
)
def test_classification_preserves_single_visual_and_small_flow(
    text: str, area: float, drawings: int, visual: bool, kind: str
) -> None:
    assert classify_page(text, area, drawings, has_visual=visual) == kind


async def test_actual_pdf_images_vectors_and_repeated_body_are_not_dropped() -> None:
    extractor = DocumentExtract(Stage2PdfExtract(0.5, 3))
    body = "테스트 범위와 경계값 검증을 수행하고 결제 실패를 재현했다.\n" * 8
    for data, expected in (
        (pdf_bytes(body, body), "A"),
        (image_pdf(), "D"),
        (pdf_bytes("작은 흐름도", visual=True), "E"),
    ):
        (result,) = await extractor.execute(
            [(ProfileDocument(type="TEST_PLAN", url="https://example.com/a.pdf"), data)]
        )
        assert result.pages[0].kind == expected
        if expected == "A":
            assert all(page.blocks for page in result.pages)
        else:
            assert result.pages[0].regions
        assert result.pages[0].number == 1


async def test_generated_table_retains_cells_and_page_provenance() -> None:
    with fitz.open() as pdf:
        page = pdf.new_page(width=400, height=500)
        for x in (40, 180, 320):
            page.draw_line(fitz.Point(x, 100), fitz.Point(x, 190))
        for y in (100, 130, 160, 190):
            page.draw_line(fitz.Point(40, y), fitz.Point(320, y))
        for row, values in enumerate((("ID", "Expected"), ("A1", "SUCCESS"), ("A2", "DENIED"))):
            for column, value in enumerate(values):
                page.insert_text((50 + column * 140, 120 + row * 30), value)
        data = pdf.tobytes()
    (document,) = await DocumentExtract(Stage2PdfExtract(0.5, 3)).execute(
        [
            (ProfileDocument(type="TEST_CASE", url="https://example.com/table.pdf"), data),
        ]
    )
    table = document.pages[0].tables[0]
    assert table.rows[2] == ("A2", "DENIED")
    assert table.row_pages == (1, 1, 1)
    assert "SUCCESS" in document.pages[0].blocks[0].text or any(
        "SUCCESS" in block.text for block in document.pages[0].blocks
    )


def test_long_page_tile_coverage_and_unread_when_eight_cannot_cover() -> None:
    regions, unread = plan_regions(1, (0, 0, 22546, 1853))
    assert not unread
    assert len(regions) <= 8
    assert regions[0].bbox[0] == 0
    assert regions[-1].bbox[2] == 22546
    assert all(right.bbox[0] < left.bbox[2] for left, right in itertools.pairwise(regions))
    assert plan_regions(1, (0, 0, 100, 10000)) == ([], True)


def test_row_cap_only_changes_model_view() -> None:
    table = TableData("doc-1/p2", (0, 0, 1, 1), tuple((str(i), "value") for i in range(200)), (2,) * 200)
    selected = select_rows(200, 80)
    assert selected[:20] == tuple(range(20))
    assert len(set(selected)) == 80
    assert selected[-1] == 199
    assert len(table.rows) == 200
    assert "전체 200행 중 80행" in markdown_table(table, 80)
    assert "199" in markdown_table(table)


def test_dense_table_tiles_cover_normal_page_with_context() -> None:
    regions, unread = plan_regions(1, (0, 0, 600, 800), dense_table=True)
    assert not unread
    assert len(regions) == 4
    assert all(region.tiled for region in regions)
    assert regions[0].bbox[:2] == (0, 0)
    assert regions[-1].bbox[2:] == (600, 800)
    assert regions[0].bbox[2] > regions[1].bbox[0]
    assert regions[0].bbox[3] > regions[2].bbox[1]


def test_overlapping_transcript_boundaries_dedup_model_view_and_preserve_original_evidence() -> None:
    repeated = "경계값 테스트에서 결제 실패를 재현했습니다."
    blocks = [
        EvidenceBlock("a", "doc-1/p1", (0, 0, 600, 450), "VISION_TRANSCRIPT", "첫 영역\n" + repeated),
        EvidenceBlock("b", "doc-1/p1", (0, 400, 600, 800), "VISION_TRANSCRIPT", repeated + "\n둘째 영역"),
    ]
    page = ExtractedPage(1, "D", (0, 0, 600, 800), blocks, [], [])
    document = ExtractedDocument("doc-1", "TEST_PLAN", None, b"", [page])
    assert page_text(document, page).count(repeated) == 1
    assert "첫 영역" in page_text(document, page)
    assert "둘째 영역" in page_text(document, page)
    assert all(repeated in block.text for block in DocumentReading([document]).blocks)


def test_visual_page_extracted_labels_are_context_only() -> None:
    label = EvidenceBlock("label", "doc-1/p1", (0, 0, 1, 1), "TEXT", "원인과 결과를 연결하는 참고 라벨")
    page = ExtractedPage(1, "E", (0, 0, 600, 800), [label], [], [])
    document = ExtractedDocument("doc-1", "TEST_PLAN", None, b"", [page])
    assert "참고 라벨 · 인용 불가" in page_text(document, page)
    assert DocumentReading([document]).blocks == []


@pytest.mark.parametrize("conflicting", [False, True])
def test_table_continuation_requires_matching_headers_and_titles(conflicting: bool) -> None:
    pages = [
        ExtractedPage(
            i,
            "A",
            (0, 0, 100, 100),
            [],
            [
                TableData(
                    f"doc-1/p{i}",
                    (0, 85 if i == 1 else 0, 100, 100 if i == 1 else 15),
                    (("ID", "값"), (str(i), "테스트")),
                    (i, i),
                    "다른 제목" if conflicting and i == 2 else "동일 제목",
                ),
            ],
            [],
        )
        for i in (1, 2)
    ]
    mark_continuations(pages)
    assert pages[1].tables[0].continued_from == (None if conflicting else "doc-1/p1")


async def test_invalid_encrypted_and_over_fifty_pages() -> None:
    extractor = DocumentExtract(Stage2PdfExtract(0.5, 3))
    source = ProfileDocument(type="PRD", url="https://example.com/a.pdf")
    for data in (b"not a pdf", pdf_bytes(*([""] * 51))):
        with pytest.raises(PipelineError) as error:
            await extractor.execute([(source, data)])
        assert error.value.status_code == 422
    with fitz.open(stream=pdf_bytes("secret"), filetype="pdf") as document:
        encrypted = document.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="reader")
    with pytest.raises(PipelineError):
        await extractor.execute([(source, encrypted)])


def test_render_resolution_and_redaction() -> None:
    region = plan_regions(1, (0, 0, 600, 800))[0][0]
    png = render_region(pdf_bytes("본문"), region, 1568)
    with Image.open(io.BytesIO(png)) as image:
        assert max(image.size) == 1568
    masked = mask_text(
        "a@company.com 010-1234-5678 02-123-4567 192.168.0.1 AKIAABCDEFGHIJKLMNOP abcdefghijklmnopqrstuvwxyz1234567890"
    )
    assert masked == "[가림:이메일] [가림:전화] [가림:전화] [가림:IP] [가림:키] [가림:키]"
    assert mask_text("+82-10-1234-5678 +82 2 123 4567") == "[가림:전화] [가림:전화]"
