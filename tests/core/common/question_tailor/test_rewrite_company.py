import json
from pathlib import Path

import pytest

from app.core.common.interview_qa.errors import PipelineError
from app.core.common.question_tailor.dto import CandidateProfile, OriginalQuestion
from app.core.common.question_tailor.prompt import SYSTEM_PROMPT_WITH_COMPANY
from app.core.common.question_tailor.rewrite import QuestionRewrite
from tests.company_helpers import RecordingCompanyClient, company_context


def questions() -> tuple[OriginalQuestion, ...]:
    return (
        OriginalQuestion(
            id=1, category="tech_choice", question="왜 Redis를 사용했나요?", expected_answer="캐시 선택 근거"
        ),
    )


async def test_without_company_matches_frozen_legacy_prompts() -> None:
    client = RecordingCompanyClient()
    rewrite = QuestionRewrite(client, "stub", 3072, 800, 600)
    await rewrite.execute(CandidateProfile(job_role="백엔드"), questions())
    expected = json.loads((Path(__file__).parent / "fixtures/rewrite_without_company.json").read_text())
    assert client.calls[0]["system"] == expected["system"]
    assert client.calls[0]["messages"][0]["content"] == expected["user"]


async def test_company_uses_separate_prompt_without_fact_ids_or_signals() -> None:
    client = RecordingCompanyClient()
    rewrite = QuestionRewrite(client, "stub", 3072, 800, 600)
    await rewrite.execute(CandidateProfile(job_role="백엔드"), questions(), company_context())
    call = client.calls[0]
    message = call["messages"][0]["content"]
    assert call["system"] == SYSTEM_PROMPT_WITH_COMPANY
    assert message.index("[지원자 사전 정보]") < message.index("[기업 정보]") < message.index("[원질문")
    assert "샘플마켓 — 온라인 거래 서비스" in message
    assert "[f:" not in message
    assert "드러나면 좋은 모습" not in message


async def test_company_alone_does_not_replace_candidate_profile() -> None:
    client = RecordingCompanyClient()
    rewrite = QuestionRewrite(client, "stub", 3072, 800, 600)
    with pytest.raises(PipelineError) as error:
        await rewrite.execute(CandidateProfile(), questions(), company_context())
    assert error.value.status_code == 422
    assert client.calls == []


def test_company_checklist_is_explicit_in_system_prompt() -> None:
    assert "- 회사 정보 밖의 회사 사실을 쓰지 않았는가?" in SYSTEM_PROMPT_WITH_COMPANY
    assert '- 소속을 나타내는 표현("저희", "우리 회사")이 없는가?' in SYSTEM_PROMPT_WITH_COMPANY
