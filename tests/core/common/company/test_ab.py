import json
from pathlib import Path

import pytest
from evaluation.company_ab import ComparisonInput, build_report, load_company_package

from app.core.common.question_tailor.dto import OriginalQuestion, TailoredQuestion
from tests.company_helpers import company_body, company_context


def test_package_is_converted_to_context_without_database_ids(tmp_path: Path) -> None:
    raw = dict(company_body(), schemaVersion=1, code="sample-commerce")
    path = tmp_path / "sample-commerce.json"
    for fact in raw["facts"]:
        del fact["factId"]
    path.write_text(json.dumps(raw))
    context = load_company_package(path)
    assert context.company_id == "sample-commerce"
    assert [fact.fact_id for fact in context.facts] == ["f1", "f2", "f3"]
    raw["schemaVersion"] = 2
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="schemaVersion"):
        load_company_package(path)


def test_report_keeps_original_order_and_marks_failed_rewrite() -> None:
    questions = (OriginalQuestion(id=1, category="choice", question="질문|<1>", expected_answer="근거"),)
    result = (TailoredQuestion(id=1, question="재작성\n둘째 줄"),)
    report = build_report(questions, [company_context(), company_context()], [result, None, result])
    assert "기업 없음 | 샘플커머스 | 샘플커머스" in report
    assert "질문&#124;&lt;1&gt;" in report
    assert "재작성<br>둘째 줄" in report
    assert "재작성 실패 · 원질문 폴백" in report


def test_default_comparison_input_has_five_valid_questions() -> None:
    path = Path(__file__).parents[4] / "evaluation/company_ab_questions.json"
    data = ComparisonInput.model_validate_json(path.read_text())
    assert len(data.questions) == 5
    assert len({question.id for question in data.questions}) == 5
