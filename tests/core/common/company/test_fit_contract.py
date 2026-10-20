import logging
from typing import Any

import pytest

from app.core.common.company.fit.evaluate import CompanyFitEvaluation, _parse_submission
from app.core.common.feedback.solo.dto import GradingTarget
from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicCallResult
from tests.company_helpers import RecordingCompanyClient, company_context, fit_output


@pytest.mark.parametrize("extra", ["score", "grade", "required", "kind", "focus", "extra"])
def test_unexpected_top_level_fields_reject_the_result(extra: str) -> None:
    raw = dict(fit_output(), **{extra: 1})
    assert _parse_submission(raw, company_context(), {"q-1"}) is None


@pytest.mark.parametrize("field", ["matched_focus", "gaps"])
@pytest.mark.parametrize("extra", ["score", "kind", "focus", "extra"])
def test_unknown_item_fields_are_removed_without_changing_other_items(field: str, extra: str) -> None:
    raw = fit_output()
    raw[field][0][extra] = "모델이 바꾼 값"
    result = _parse_submission(raw, company_context(), {"q-1"})
    assert result is not None
    assert (result.matched_focus if field == "matched_focus" else result.gaps) == []


@pytest.mark.parametrize("ids", [[], ["unknown"], ["unanswered"], ["q-1", 1]])
def test_all_invalid_evidence_discards_comment_instead_of_exposing_it(ids: list[Any]) -> None:
    raw = fit_output()
    for field in ("matched_focus", "gaps"):
        raw[field][0]["question_ids"] = ids
    assert _parse_submission(raw, company_context(), {"q-1"}) is None


@pytest.mark.parametrize("field", ["matched_focus", "gaps"])
def test_invalid_first_reference_does_not_hide_later_valid_reference(field: str) -> None:
    raw = fit_output()
    valid = raw[field][0]
    raw[field] = [dict(valid, question_ids=["unanswered"]), valid]
    result = _parse_submission(raw, company_context(), {"q-1"})
    assert result is not None
    items = result.matched_focus if field == "matched_focus" else result.gaps
    assert len(items) == 1
    assert items[0].question_ids == ["q-1"]


def test_question_ids_are_opaque_and_deduplicated_in_original_order() -> None:
    raw = fit_output()
    for field in ("matched_focus", "gaps"):
        raw[field][0]["question_ids"] = ["-1", "q2", "unanswered", "-1", "0003"]
    result = _parse_submission(raw, company_context(), {"-1", "q2", "0003"})
    assert result is not None
    assert result.matched_focus[0].question_ids == ["-1", "q2", "0003"]
    assert result.gaps[0].question_ids == ["-1", "q2", "0003"]


@pytest.mark.parametrize("stop", [None, "end_turn", "max_tokens", "refusal", "pause_turn", "stop_sequence"])
async def test_non_tool_stop_reason_is_never_accepted(stop: str | None, monkeypatch: pytest.MonkeyPatch) -> None:
    client = RecordingCompanyClient()

    async def call(**kwargs: Any) -> AnthropicCallResult:
        return AnthropicCallResult(
            content_blocks=[{"type": "tool_use", "name": "submit_company_fit", "input": fit_output()}],
            stop_reason=stop,
            input_tokens=1,
            output_tokens=2,
        )

    monkeypatch.setattr(client, "call", call)
    assert await CompanyFitEvaluation(client, "stub", 1500, 0.2, 3000).execute((), company_context(), None) is None


async def test_multiple_tool_submissions_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    client = RecordingCompanyClient()

    async def call(**kwargs: Any) -> AnthropicCallResult:
        block = {"type": "tool_use", "name": "submit_company_fit", "input": fit_output()}
        return AnthropicCallResult(
            content_blocks=[block, block], stop_reason="tool_use", input_tokens=1, output_tokens=2
        )

    monkeypatch.setattr(client, "call", call)
    assert await CompanyFitEvaluation(client, "stub", 1500, 0.2, 3000).execute((), company_context(), None) is None


async def test_completion_logs_usage_and_counts_without_raw_text(caplog: pytest.LogCaptureFixture) -> None:
    client = RecordingCompanyClient()
    company = company_context().model_copy(update={"display_name": "비공개 기업"})
    target = GradingTarget(
        question_id="q-1", type="ORIGINAL", content="비공개 질문", intention="의도", answer="비공개 답변"
    )
    with caplog.at_level(logging.INFO):
        result = await CompanyFitEvaluation(client, "stub", 1500, 0.2, 3000).execute((target,), company, None)
    assert result is not None
    (record,) = [record for record in caplog.records if record.getMessage().startswith("company_fit.completed")]
    assert record.__dict__["applied"] is True
    assert record.__dict__["matched_count"] == record.__dict__["gap_count"] == 1
    assert record.__dict__["input_tokens"] >= 0
    assert record.__dict__["output_tokens"] >= 0
    assert record.__dict__["elapsed_ms"] >= 0
    assert "비공개" not in caplog.text
    assert result.comment not in caplog.text
