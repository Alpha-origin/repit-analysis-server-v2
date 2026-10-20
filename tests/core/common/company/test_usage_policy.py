import pytest

from app.core.common.company.dto import CompanyFact, CompanyFactKind
from app.core.common.company.fit.prompt import SYSTEM_PROMPT as FIT_PROMPT
from app.core.common.company.prompt import ROLE_FACT_KINDS, build_company_block
from app.core.common.interview_qa.dto import ProjectSummary
from app.core.common.question_tailor.dto import CandidateProfile, OriginalQuestion
from app.core.common.question_tailor.multi.dto import TailorPersona
from app.core.common.question_tailor.multi.prompt import (
    SYSTEM_PROMPT_WITH_COMPANY as MULTI_PROMPT,
    build_generate_user_message,
)
from app.core.common.question_tailor.prompt import (
    SYSTEM_PROMPT_WITH_COMPANY as REWRITE_PROMPT,
    build_rewrite_user_message,
)
from tests.company_helpers import company_context


def test_rewrite_selection_keeps_original_scope_and_falls_back_when_unrelated() -> None:
    priorities = [
        "의도와 같은 핵심 답변",
        "주력 사업(BUSINESS)",
        "기술 스택(TECH_STACK)",
        "인재상(TALENT)",
        "원문에 가깝게 유지",
    ]
    offsets = [REWRITE_PROMPT.index(text) for text in priorities]
    assert offsets == sorted(offsets)
    assert "회사 관점을 모든 질문에 억지로 넣지 마라" in REWRITE_PROMPT
    assert "기업 스택으로 질문의 기술 대상을 바꾸지 마라" in REWRITE_PROMPT
    assert "참고 답안을 질문의 힌트로 옮기지 마라" in REWRITE_PROMPT


def test_shared_scope_rules_do_not_invent_parent_or_current_company_facts() -> None:
    for prompt in (REWRITE_PROMPT, MULTI_PROMPT):
        assert "특정 팀·서비스·과거 공개 사례" in prompt
        assert "다른 법인·부모 회사 사실을 상속하지 마라" in prompt
        assert "category는 표시·맥락용" in prompt
        assert "외부 자료로 범위를 보충하지 마라" in prompt
    assert "프로젝트 정보가 없는 경험은 명시적인 가정형" in MULTI_PROMPT
    assert "직책별로 선택한 종류의 fact가 없으면" in MULTI_PROMPT


def test_fit_distinguishes_observed_concern_missing_explanation_and_no_opportunity() -> None:
    assert "긍정 행동, 직접 말한 우려 행동, 관련 설명 부족, 관찰 기회 없음" in FIT_PROMPT
    assert "설명하지 않았다는 이유로 실제 행동을 하지 않았다고 단정하지 마라" in FIT_PROMPT
    assert "총평은 관찰 한계를 중립적으로 설명" in FIT_PROMPT
    assert "미답변 문항은 근거로 사용하지 마라" in FIT_PROMPT


@pytest.mark.parametrize("missing", ["MISSION_VISION", "PROBLEM", "TECH_STACK"])
@pytest.mark.parametrize("role", ["hr", "ceo", "pm", "design"])
def test_missing_optional_fact_kinds_do_not_create_facts(role: str, missing: CompanyFactKind) -> None:
    company = company_context().model_copy(
        update={"facts": tuple(fact for fact in company_context().facts if fact.kind != missing)}
    )
    expected = tuple(fact for fact in company.facts if fact.kind in ROLE_FACT_KINDS[role])
    block = build_company_block(company, ROLE_FACT_KINDS[role], include_ids=True)
    assert len(block) == len(expected) + 3
    assert all(any(fact.content in line for line in block) for fact in expected)
    message = build_generate_user_message(
        (TailorPersona(persona_id="p1", role=role.upper(), question_count=1),),
        ProjectSummary(overview="프로젝트 정보 부족", repositories=[], core_features=[], tech_stack=[]),
        (),
        600,
        company,
    )
    assert "회사 관점:" in message
    assert "프로젝트 정보 부족" in message


def test_scoped_content_and_unrelated_stack_are_passed_as_data_without_replacing_question() -> None:
    scoped = "과거 샘플마켓 결제 팀의 재시도 사례"
    company = company_context().model_copy(
        update={
            "display_name": "R&D",
            "facts": (
                *company_context().facts,
                CompanyFact(fact_id="tech", kind="TECH_STACK", title="UI", content="일부 서비스의 React 적용"),
                CompanyFact(fact_id="past", kind="PROBLEM", title="재시도", content=scoped),
            ),
        }
    )
    question = OriginalQuestion(
        id=7, category="cache", question="Redis를 왜 선택했나요?", expected_answer="캐시 선택 근거"
    )
    message = build_rewrite_user_message(CandidateProfile(job_role="백엔드"), (question,), 600, company)
    assert scoped in message
    assert "일부 서비스의 React 적용" in message
    assert "Redis를 왜 선택했나요?" in message
    assert "R&D" in message
    assert company.facts[-1].content == scoped
