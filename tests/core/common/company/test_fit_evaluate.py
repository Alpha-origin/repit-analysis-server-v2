from typing import Any

import pytest

from app.core.common.company.fit.evaluate import CompanyFitEvaluation, _parse_submission
from app.core.common.company.fit.prompt import build_fit_user_message
from app.core.common.company.fit.tools import SUBMIT_COMPANY_FIT_TOOL
from app.core.common.feedback.solo.dto import GradingTarget
from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicCallResult
from tests.company_helpers import RecordingCompanyClient, company_context, fit_output


def test_unknown_references_are_removed_and_labels_come_from_server() -> None:
    raw = fit_output()
    raw["matched_focus"] += [{"fact_id": "unknown", "evidence": "가짜", "question_ids": ["q-1"]}]
    raw["matched_focus"][0].update(kind="BUSINESS", focus="가짜 이름", question_ids=["q-1", "no-answer", "q-1"])
    raw["gaps"][0]["question_ids"] = ["unknown"]
    result = _parse_submission(raw, company_context(), {"q-1"})
    assert result is not None
    assert result.matched_focus[0].model_dump(by_alias=True) == {
        "factId": "t1",
        "kind": "TALENT",
        "focus": "오너십",
        "evidence": "문제를 직접 해결했습니다",
        "questionIds": ["q-1"],
    }
    assert len(result.matched_focus) == 1
    assert result.gaps[0].question_ids == []


@pytest.mark.parametrize("field", ["matched_focus", "gaps"])
async def test_items_are_filtered_before_three_item_limit(field: str) -> None:
    raw = fit_output()
    entry = raw[field][0]
    text_key = "evidence" if field == "matched_focus" else "suggestion"
    raw[field] = [dict(entry, **{text_key: "가" * 151}), dict(entry, fact_id="missing")]
    raw[field] += [dict(entry, **{text_key: str(index)}) for index in range(4)]
    result = _parse_submission(raw, company_context(), {"q-1"})
    assert result is not None
    values = result.matched_focus if field == "matched_focus" else result.gaps
    assert len(values) == 3
    assert [value.model_dump()[text_key] for value in values] == ["0", "1", "2"]


@pytest.mark.parametrize("comment", ["", " ", "가" * 301, None, 123])
def test_invalid_comment_discards_entire_fit(comment: object) -> None:
    raw = fit_output()
    raw["comment"] = comment
    assert _parse_submission(raw, company_context(), {"q-1"}) is None


@pytest.mark.parametrize("entry", [None, {}, {"fact_id": "t1", "evidence": "근거", "question_ids": [1]}])
def test_malformed_items_do_not_poison_other_items(entry: object) -> None:
    raw = fit_output()
    raw["matched_focus"].insert(0, entry)
    result = _parse_submission(raw, company_context(), {"q-1"})
    assert result is not None
    assert len(result.matched_focus) == 1


@pytest.mark.parametrize("key", ["matched_focus", "gaps"])
def test_malformed_top_level_array_discards_fit(key: str) -> None:
    raw = fit_output()
    raw[key] = None
    assert _parse_submission(raw, company_context(), {"q-1"}) is None


def test_boundary_lengths_and_empty_arrays_are_valid() -> None:
    raw = fit_output()
    raw["comment"] = "가" * 300
    raw["matched_focus"][0]["evidence"] = "가" * 150
    raw["gaps"][0]["suggestion"] = "가" * 150
    assert _parse_submission(raw, company_context(), {"q-1"}) is not None
    raw.update(matched_focus=[], gaps=[])
    assert _parse_submission(raw, company_context(), {"q-1"}) is not None


@pytest.mark.parametrize("case", ["missing", "wrong", "truncated"])
async def test_no_tool_submission_or_truncation_returns_none(case: str, monkeypatch: pytest.MonkeyPatch) -> None:
    client = RecordingCompanyClient()
    evaluator = CompanyFitEvaluation(client, "stub", 1500, 0.2, 10)

    async def call(**kwargs: Any) -> AnthropicCallResult:
        blocks = (
            []
            if case == "missing"
            else [
                {
                    "type": "tool_use",
                    "name": "wrong" if case == "wrong" else "submit_company_fit",
                    "input": fit_output(),
                }
            ]
        )
        return AnthropicCallResult(
            content_blocks=blocks,
            input_tokens=0,
            output_tokens=0,
            stop_reason="max_tokens" if case == "truncated" else "tool_use",
        )

    monkeypatch.setattr(client, "call", call)
    assert await evaluator.execute((), company_context(), None) is None


def test_fit_input_has_fact_ids_tone_and_bounded_answer() -> None:
    target = GradingTarget(
        question_id="q-1", type="ORIGINAL", intention="선택 근거", content="왜 선택했나요", answer="답변" * 10
    )
    message = build_fit_user_message((target,), company_context(), "DIRECT", 4)
    assert "[f:t1]" in message
    assert "드러나면 좋은 모습" in message
    assert "어조(DIRECT)" in message
    assert "성향(" not in message
    assert "답변: 답변답변...(이하 생략)" in message
    assert "답변" * 10 not in message
    for key in ["matched_focus", "gaps"]:
        fields = SUBMIT_COMPANY_FIT_TOOL["input_schema"]["properties"][key]["items"]["properties"]
        assert "focus" not in fields
        assert "kind" not in fields
