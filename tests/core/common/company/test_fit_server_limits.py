import pytest

from app.core.common.company.dto import CompanyFact
from app.core.common.company.fit.dto import FIT_COMMENT_MAX, FIT_ITEM_TEXT_MAX, FIT_ITEMS_MAX
from app.core.common.company.fit.evaluate import _parse_submission
from tests.company_helpers import company_context, fit_output


@pytest.mark.parametrize("field", ["matched_focus", "gaps"])
def test_server_keeps_only_first_three_valid_distinct_facts(field: str) -> None:
    company = company_context()
    company = company.model_copy(
        update={
            "facts": (
                *company.facts,
                CompanyFact(
                    fact_id="fourth",
                    kind="BUSINESS",
                    title="추가 서비스",
                    content="네 번째 자료",
                ),
            )
        }
    )
    raw = fit_output()
    text_key = "evidence" if field == "matched_focus" else "suggestion"
    raw[field] = [{"fact_id": fact.prompt_id, text_key: "답변 근거", "question_ids": ["q-1"]} for fact in company.facts]
    result = _parse_submission(raw, company, {"q-1"})
    assert result is not None
    items = result.matched_focus if field == "matched_focus" else result.gaps
    assert len(items) == FIT_ITEMS_MAX
    assert [item.fact_id for item in items] == [fact.fact_id for fact in company.facts[:FIT_ITEMS_MAX]]


@pytest.mark.parametrize("length", [FIT_COMMENT_MAX, FIT_COMMENT_MAX + 1])
def test_comment_length_is_still_checked_by_server(length: int) -> None:
    raw = fit_output()
    raw["comment"] = "가" * length
    result = _parse_submission(raw, company_context(), {"q-1"})
    assert (result is not None) is (length <= FIT_COMMENT_MAX)


@pytest.mark.parametrize("field", ["matched_focus", "gaps"])
@pytest.mark.parametrize("length", [FIT_ITEM_TEXT_MAX, FIT_ITEM_TEXT_MAX + 1])
def test_item_length_is_still_checked_by_server(field: str, length: int) -> None:
    raw = fit_output()
    text_key = "evidence" if field == "matched_focus" else "suggestion"
    raw[field][0][text_key] = "가" * length
    result = _parse_submission(raw, company_context(), {"q-1"})
    assert result is not None
    items = result.matched_focus if field == "matched_focus" else result.gaps
    assert len(items) == (1 if length <= FIT_ITEM_TEXT_MAX else 0)
