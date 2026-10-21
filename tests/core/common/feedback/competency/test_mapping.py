import pytest
from tests.multi_helpers import feedback_body

from app.core.common.feedback.competency.catalog import Competency
from app.core.common.feedback.competency.mapping import (
    DEV_TABLE,
    JOB_TABLES,
    NON_TECH_TABLE,
    infer_targets,
    resolve_targets,
)
from app.core.common.feedback.solo.answer_assembly import AnswerAssembly
from app.core.common.feedback.solo.dto import FeedbackSoloRequest
from app.core.common.job_spec import JOBS
from app.inbound.http.interview_feedback.multi.dto import MultiInterviewQuestionRequest
from app.inbound.http.interview_feedback.solo.dto import FeedbackRequest, InterviewQuestionRequest


@pytest.mark.parametrize("job", list(JOBS))
def test_all_job_categories_are_mapped(job: str) -> None:
    table = JOB_TABLES.get(job, DEV_TABLE)
    assert set(JOBS[job].categories) == set(table)
    for category, (primary, secondary) in table.items():
        target = resolve_targets(category, job)
        assert (target.primary, target.secondary, target.source) == (primary, secondary, "job")


@pytest.mark.parametrize("category", list(NON_TECH_TABLE))
def test_non_tech_table_is_used_for_all_non_tech_roles(category: str) -> None:
    for role in ("HR", "ceo", "PM", "DESIGN", "UNKNOWN"):
        target = resolve_targets(category, "BACKEND", role)
        assert (target.primary, target.secondary) == NON_TECH_TABLE[category]
        assert Competency.KNOWLEDGE not in (target.primary, *target.secondary)


def test_same_category_prefers_job_context() -> None:
    assert resolve_targets("performance", "DBA_INFRA").primary == Competency.PROBLEM_SOLVING
    assert resolve_targets("performance", "GEN_STRATEGY").primary == Competency.KNOWLEDGE
    assert resolve_targets("collaboration", "PRODUCT_MANAGER").secondary == (Competency.OWNERSHIP,)
    assert resolve_targets("collaboration", "GEN_DESIGN").secondary == (Competency.GROWTH,)
    assert resolve_targets("collaboration", "PRODUCT_MANAGER", "HR").secondary == (Competency.GROWTH,)


@pytest.mark.parametrize("category", [None, "질문 의도 문장", "x" * 65, "bad-category", 100, {}, ""])
@pytest.mark.parametrize("dto", [InterviewQuestionRequest, MultiInterviewQuestionRequest])
def test_invalid_category_is_accepted_as_unclassified(
    category: object, dto: type[InterviewQuestionRequest] | type[MultiInterviewQuestionRequest]
) -> None:
    question = {**feedback_body()["questions"][0], "category": category}
    assert dto.model_validate(question).category is None


def test_follow_inherits_normalized_parent_category() -> None:
    body = feedback_body()
    original = {**body["questions"][0], "category": " TECH_CHOICE "}
    follow = {
        **original,
        "questionId": "follow",
        "parentId": original["questionId"],
        "type": "FOLLOW",
        "category": None,
    }
    body["questions"] = [original, follow]
    body["answers"].append({**body["answers"][0], "answerId": "follow-a", "questionId": "follow"})
    assembled = AnswerAssembly().execute(
        FeedbackSoloRequest.model_validate(FeedbackRequest.model_validate(body).model_dump(mode="json"))
    )
    assert [t.category for t in assembled.targets] == ["tech_choice", "tech_choice"]


def test_legacy_category_and_non_tech_inference() -> None:
    assert resolve_targets("기술 선택 이유 확인").primary is None
    assert infer_targets("KNOWLEDGE", non_tech=True).primary is None
    assert infer_targets("GROWTH", non_tech=True).source == "inferred"
