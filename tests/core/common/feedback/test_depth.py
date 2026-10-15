import pytest

from app.core.common.feedback.depth import depth_level_from_evidence, evidence_sources
from app.core.common.feedback.scoring import AxisLevels, parse_grading_levels

_QUOTES = {
    "reason": "대기 시간을 줄이려고 비동기로 바꿨습니다.",
    "alternatives": "동기 방식보다 대기가 짧았습니다.",
    "limitation": "트래픽이 적으면 이점이 작습니다.",
    "generic_reason": "좋아서 선택했습니다.",
}
_SOURCES = tuple(_QUOTES.values())


def _evidence(*keys: str) -> dict[str, str]:
    return {key: value if key in keys else "" for key, value in _QUOTES.items()}


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        ((), 0),
        (("generic_reason",), 1),
        (("reason",), 2),
        (("reason", "alternatives"), 3),
        (("reason", "limitation"), 3),
        (("reason", "alternatives", "limitation"), 4),
        (("alternatives", "limitation"), 0),
        (("reason", "generic_reason"), 2),
    ],
)
def test_depth_level_is_derived_from_evidence_presence(keys: tuple[str, ...], expected: int) -> None:
    assert depth_level_from_evidence(_evidence(*keys), _SOURCES) == expected


@pytest.mark.parametrize(
    "raw", [None, {}, {"reason": "근거"}, {**_evidence(), "extra": ""}, {**_evidence(), "reason": True}]
)
def test_invalid_evidence_is_rejected(raw: object) -> None:
    assert depth_level_from_evidence(raw, _SOURCES) is None


def test_invented_quote_is_rejected_instead_of_silently_scored_zero() -> None:
    assert depth_level_from_evidence({**_evidence(), "reason": "비용이 낮아서 선택했습니다."}, _SOURCES) is None


def test_quote_from_another_question_is_not_valid_evidence() -> None:
    assert depth_level_from_evidence(_evidence("reason"), ("캐시를 사용했습니다.",)) is None


def test_parent_answer_and_visible_truncation_are_respected() -> None:
    sources = evidence_sources("첫 문장 뒤 숨은 이유", "부모 답변", 4)
    assert sources == ("첫 문장", "부모 답")
    assert depth_level_from_evidence({**_evidence(), "reason": "부모 답"}, sources) == 2
    assert depth_level_from_evidence({**_evidence(), "reason": "숨은 이유"}, sources) is None


def test_whitespace_changes_do_not_invalidate_an_otherwise_verbatim_quote() -> None:
    raw = {**_evidence(), "reason": "대기 시간을\n줄이려고"}
    assert depth_level_from_evidence(raw, ("대기   시간을 줄이려고 변경했습니다.",)) == 2


def test_computed_depth_is_used_and_numeric_model_depth_is_not_accepted() -> None:
    scores = {"intent": 4, "specificity": 3, "accuracy": None}
    assert parse_grading_levels(scores, _evidence("reason"), _SOURCES) == AxisLevels(4, 2, 3, None)
    assert parse_grading_levels({**scores, "depth": 4}, _evidence("reason"), _SOURCES) is None


def test_intent_zero_still_overrides_computed_depth() -> None:
    scores = {"intent": 0, "specificity": 4, "accuracy": 4}
    assert parse_grading_levels(scores, _evidence("reason"), _SOURCES) == AxisLevels(0, 0, 0, None)
