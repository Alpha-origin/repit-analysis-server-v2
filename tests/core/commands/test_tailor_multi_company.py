import json
from pathlib import Path

from app.core.common.interview_qa.dto import ProjectSummary
from app.core.common.question_tailor.dto import OriginalQuestion
from app.core.common.question_tailor.multi.dto import TailorPersona
from app.core.common.question_tailor.multi.generate import MultiQuestionGenerate
from app.core.common.question_tailor.multi.prompt import SYSTEM_PROMPT_WITH_COMPANY, build_generate_user_message
from tests.company_helpers import RecordingCompanyClient, company_body, company_context, company_http
from tests.multi_helpers import tailor_body


async def test_company_reaches_both_rewrite_and_generation() -> None:
    body = tailor_body()
    body["company"] = company_body()
    async with company_http() as (client, webhook, llm):
        response = await client.post("/questions/tailor/multi", json=body)
    assert response.status_code == 202
    assert webhook.payloads[0]["status"] == "succeeded"
    assert len(llm.calls) == 2
    assert all("[기업 정보] 샘플커머스" in call["messages"][0]["content"] for call in llm.calls)
    generate = next(call for call in llm.calls if call["tool_choice"]["name"] == "submit_generated_questions")
    message = generate["messages"][0]["content"]
    assert generate["system"] == SYSTEM_PROMPT_WITH_COMPANY
    assert "회사 관점: 인재상·핵심 가치" in message
    assert "회사 관점: 미션·비전, 주력 사업·서비스, 인재상·핵심 가치" in message
    assert "회사 관점: 주력 사업·서비스, 공개 사례의 문제" in message
    assert "드러나면 좋은 모습" in message


async def test_without_company_generation_matches_frozen_legacy_prompts() -> None:
    llm = RecordingCompanyClient()
    await MultiQuestionGenerate(llm, "stub", 4608, 600, 600).execute(
        (TailorPersona(persona_id="hr", role="HR", question_count=2),),
        ProjectSummary(overview="주문 서비스", repositories=[], core_features=[], tech_stack=[]),
        (
            OriginalQuestion(
                id=1, category="tech_choice", question="왜 Redis를 사용했나요?", expected_answer="캐시 선택 근거"
            ),
        ),
    )
    fixture = Path(__file__).parents[1] / "common/question_tailor/fixtures/generate_without_company.json"
    expected = json.loads(fixture.read_text())
    assert llm.calls[0]["system"] == expected["system"]
    assert llm.calls[0]["messages"][0]["content"] == expected["user"]


def test_design_and_unknown_role_get_company_focus_and_block_follows_project() -> None:
    message = build_generate_user_message(
        (
            TailorPersona(persona_id="d", role=" design ", question_count=2),
            TailorPersona(persona_id="new", role="NEW", question_count=2),
        ),
        ProjectSummary(overview="주문 서비스", repositories=[], core_features=[], tech_stack=[]),
        (),
        600,
        company_context(),
    )
    assert "회사 관점: 주력 사업·서비스" in message
    assert "회사 관점: 인재상·핵심 가치, 주력 사업·서비스" in message
    assert message.index("주문 서비스") < message.index("[기업 정보]") < message.index("[기술 면접관")
