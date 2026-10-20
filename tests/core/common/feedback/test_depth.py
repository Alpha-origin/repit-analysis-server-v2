import pytest

from app.core.common.feedback.depth import depth_evidence_check, depth_level_from_evidence, evidence_sources
from app.core.common.feedback.scoring import AxisLevels, grading_drop_reason, parse_grading_levels

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


# 이슈 #46: 실제 면접 답변에서 모델이 떨어진 문장을 한 인용으로 이어 붙여 피드백 전체가 실패했다.
_ANSWER = (
    "로그아웃 뒤 전체 캐시를 지웠습니다. 사용자별 키로만 나누면 진행 중 요청이 남을 위험이 있었습니다. "
    "먼저 요청을 취소하고 세션을 끝냈습니다. 공개 데이터만 따로 두면 일부 캐시를 다시 쓸 수 있습니다."
)


def test_quote_joining_separate_verbatim_sentences_is_accepted() -> None:
    quote = "사용자별 키로만 나누면 진행 중 요청이 남을 위험이 있었습니다. 공개 데이터만 따로 두면"
    assert depth_evidence_check({**_evidence(), "reason": quote}, (_ANSWER,)) == (2, None)


def test_paraphrased_quote_is_still_rejected_with_its_key() -> None:
    raw = {**_evidence(), "reason": "개인정보 보호를 우선했습니다."}
    assert depth_evidence_check(raw, (_ANSWER,)) == (None, "evidence_mismatch:reason")


def test_joined_quote_with_one_invented_sentence_is_rejected() -> None:
    quote = "먼저 요청을 취소하고 세션을 끝냈습니다. 서버 비용도 줄였습니다."
    assert depth_level_from_evidence({**_evidence(), "reason": quote}, (_ANSWER,)) is None


def test_joined_sentences_must_come_from_the_same_answer() -> None:
    sources = ("캐시를 지웠습니다. 다른 이야기입니다.", "요청을 취소했습니다.")
    raw = {**_evidence(), "reason": "캐시를 지웠습니다. 요청을 취소했습니다."}
    assert depth_level_from_evidence(raw, sources) is None


def test_curly_quotes_and_ellipsis_do_not_invalidate_a_verbatim_quote() -> None:
    raw = {**_evidence(), "reason": "\u201c먼저 요청을 취소하고\u2026\u201d"}
    assert depth_level_from_evidence(raw, (_ANSWER,)) == 2


def test_punctuation_only_quote_counts_as_empty() -> None:
    raw = {**_evidence(), "reason": " ... "}
    assert depth_level_from_evidence(raw, (_ANSWER,)) == 0


def test_drop_reason_distinguishes_scores_and_evidence() -> None:
    good = {"intent": 4, "specificity": 3, "accuracy": None}
    assert grading_drop_reason({**good, "depth": 2}, _evidence("reason"), _SOURCES) == "invalid_scores"
    assert grading_drop_reason({**good, "intent": 9}, _evidence("reason"), _SOURCES) == "invalid_scores"
    assert grading_drop_reason(good, {"reason": ""}, _SOURCES) == "evidence_format"
    assert (
        grading_drop_reason(good, {**_evidence(), "limitation": "없는 문장"}, _SOURCES)
        == "evidence_mismatch:limitation"
    )


def test_decimal_numbers_are_not_split_into_matchable_fragments() -> None:
    raw = {**_evidence(), "reason": "응답이 3.5배 빨라졌습니다"}
    assert depth_level_from_evidence(raw, ("응답이 3초 줄었고 처리량은 5배 빨라졌습니다.",)) is None
    assert depth_level_from_evidence(raw, ("배포 뒤 응답이 3.5배 빨라졌습니다.",)) == 2


def test_joined_sentences_must_keep_the_answer_order() -> None:
    quote = "공개 데이터만 따로 두면 일부 캐시를 다시 쓸 수 있습니다. 먼저 요청을 취소하고 세션을 끝냈습니다."
    assert depth_level_from_evidence({**_evidence(), "reason": quote}, (_ANSWER,)) is None
