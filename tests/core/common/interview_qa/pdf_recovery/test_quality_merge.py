import pytest

from app.core.common.interview_qa.dto import ImageBlock, ParsedPortfolio, TextBlock
from app.core.common.interview_qa.pdf_geometry import union_area
from app.core.common.interview_qa.pdf_text_merge import merge_page_text
from app.core.common.interview_qa.stage2_text_clean import Stage2TextClean
from app.core.common.interview_qa.stage2_text_quality import Stage2TextQuality, is_broken
from tests.pdf_recovery_helpers import page


@pytest.mark.parametrize(
    "text",
    [
        "한글 원문 개발 프로젝트 기능 설명 " * 10,
        "English code foo() {} " * 10,
        "123 = 42; <> @ & + " * 10,
        "\ufffd" * 4 + "ok" * 30,
    ],
)
async def test_good_native_and_normal_whitespace_do_not_trigger_recovery(text: str) -> None:
    assert await Stage2TextQuality().execute(ParsedPortfolio(pages=[page(text=text)])) == []
    assert not is_broken("hello\n\t " * 10)
    assert is_broken("\x1c" * 5 + "damaged")


async def test_native_error_empty_broken_and_missing_scan_body_are_candidates() -> None:
    pages = [page(0), page(1, "\ufffd" * 8 + "bad"), page(2, "TITLE"), page(3, "ok" * 150)]
    pages[3].native_extraction_error = "native_extraction_error"
    pages[2].image_blocks = [ImageBlock(image_bytes=b"png", width=100, height=100, bbox=(0, 0, 100, 100), xref=1)]
    plans = await Stage2TextQuality().execute(ParsedPortfolio(pages=pages))
    assert [plan.page_number for plan in plans] == [0, 1, 2, 3]
    assert "broken_characters" in plans[1].reasons
    assert "partial_extraction" in plans[2].reasons
    assert plans[2].priority > plans[1].priority
    assert union_area([(0, 0, 60, 100), (40, 0, 100, 100)]) == 10_000


def test_native_preserved_and_missing_text_inserted_with_sources() -> None:
    target = page(text="Native heading")
    native = target.raw_text_blocks[0].model_copy(deep=True)
    merge_page_text(
        target,
        [
            TextBlock(text="Native heading", bbox=(10, 10, 90, 30), source="vision"),
            TextBlock(text="Missing 12.5 ms", bbox=(10, 40, 90, 70), source="vision"),
        ],
    )
    assert [block.text for block in target.text_blocks] == ["Native heading", "Missing 12.5 ms"]
    assert target.raw_text_blocks == [native]
    assert target.text_source == "mixed"


def test_corruption_replaced_only_at_same_verified_region() -> None:
    target = page(text="\ufffd" * 10)
    merge_page_text(target, [TextBlock(text="Fixed", bbox=(10, 10, 90, 30), source="vision")])
    assert target.text_blocks[0].text == "Fixed"
    assert target.raw_text_blocks[0].text == "\ufffd" * 10
    assert not target.text_recovery.unreadable_regions
    other = page(text="\ufffd" * 10)
    merge_page_text(other, [TextBlock(text="Elsewhere", bbox=(10, 60, 90, 80), source="vision")])
    assert (10, 10, 90, 30) in other.text_recovery.unreadable_regions


def test_conflicts_keep_native_and_unsafe_column_insertions_remain_unreadable() -> None:
    target = page(text="42 ms", bbox=(10, 10, 45, 30))
    merge_page_text(
        target,
        [
            TextBlock(text="420 ms", bbox=(10, 10, 45, 30), source="vision"),
            TextBlock(text="Unordered column", bbox=(60, 10, 90, 30), source="vision"),
        ],
    )
    assert [block.text for block in target.text_blocks] == ["42 ms"]
    assert set(target.text_recovery.diagnostics) == {"native_vision_conflict", "ambiguous_reading_order"}


def test_same_words_only_deduplicated_at_overlapping_positions() -> None:
    target = page()
    merge_page_text(
        target,
        [
            TextBlock(text="Repeat", bbox=box, source="vision")
            for box in [(10, 10, 90, 30), (10, 12, 90, 32), (10, 60, 90, 80)]
        ],
    )
    assert len(target.text_blocks) == 2


async def test_cleanup_preserves_raw_source_and_recovery_diagnostics() -> None:
    target = page(text="contact@example.com " * 5)
    target.text_blocks[0].source = "vision"
    target.text_source = "vision"
    target.text_recovery.state = "recovered"
    target.text_recovery.evaluated = True
    result = await Stage2TextClean(0.5, 3).execute(ParsedPortfolio(pages=[target]))
    cleaned = result.pages[0]
    assert not cleaned.text_blocks
    assert "contact@example.com" in cleaned.raw_text_blocks[0].text
    assert cleaned.text_recovery.state == "recovered"
    assert cleaned.text_source == "vision"
    assert "cleanup_suspect" in cleaned.text_recovery.diagnostics


def test_multi_column_native_order_not_rearranged_by_recovery() -> None:
    target = page()
    target.raw_text_blocks = [
        TextBlock(text=text, bbox=box)
        for text, box in [
            ("Left top", (0, 10, 45, 20)),
            ("Left bottom", (0, 60, 45, 70)),
            ("Right top", (60, 10, 95, 20)),
        ]
    ]
    merge_page_text(target, [])
    assert [block.text for block in target.text_blocks] == ["Left top", "Left bottom", "Right top"]
