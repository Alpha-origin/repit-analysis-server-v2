from copy import deepcopy
from typing import Any, NoReturn

import pytest

from app.core.common.interview_qa.errors import PipelineError
from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicTextClientError
from app.core.common.question_cycle.dto import QuestionCycleRequest
from app.core.common.question_cycle.generate import QuestionCycleGenerate
from tests.cycle_helpers import SequencedClient, refined_profile, solo_submissions


def request(exclude: tuple[str, ...] = ()) -> QuestionCycleRequest:
    return QuestionCycleRequest(
        profile=refined_profile(), mode="SOLO", exclude_questions=exclude, callback_url="https://example.com/callback"
    )


def generator(client: SequencedClient) -> QuestionCycleGenerate:
    return QuestionCycleGenerate(client, "stub", 12288, 1, 400, 30, 600)


async def test_later_sets_exclude_all_accepted_questions_without_truncating_them() -> None:
    batches = solo_submissions()
    client = SequencedClient(batches)
    recent = tuple(f"이전 질문 {number}" for number in range(35))
    result = await generator(client).execute(request(recent))

    assert len(result) == 15
    assert len(client.calls) == 3
    for index, call in enumerate(client.calls):
        prompt = call["messages"][0]["content"]
        assert "이전 질문 29" in prompt
        assert "이전 질문 30" not in prompt
        for question in [q for batch in batches[:index] for q in batch["questions"]]:
            assert question["question"] in prompt
        schema = call["tools"][0]["input_schema"]["properties"]["questions"]
        assert schema["minItems"] == schema["maxItems"] == 5
        assert schema["items"]["properties"]["set_no"]["enum"] == [index + 1]
        assert call["max_tokens"] == 12288


async def test_cross_batch_duplicate_retries_only_the_bad_set(caplog: pytest.LogCaptureFixture) -> None:
    batches = solo_submissions()
    repeated = deepcopy(batches[1])
    repeated["questions"][0]["question"] = batches[0]["questions"][0]["question"]
    client = SequencedClient([batches[0], repeated, *batches[1:]])
    result = await generator(client).execute(request(), job_id="cycle-job-4")

    assert len(result) == 15
    assert len(client.calls) == 4
    feedback = client.calls[2]["messages"][-1]["content"][0]
    assert feedback["tool_use_id"] == "t-2"
    assert "최근 질문과 같다" in feedback["content"]
    log = next(record.getMessage() for record in caplog.records if "generate.violations" in record.getMessage())
    assert "job_id=cycle-job-4" in log
    assert "sets=(2,)" in log
    assert "최근 질문과 같다" in log
    assert repeated["questions"][0]["question"] not in log


async def test_parseable_but_truncated_tool_is_discarded_and_retried_fresh(caplog: pytest.LogCaptureFixture) -> None:
    batches = solo_submissions()
    truncated = deepcopy(batches[0])
    truncated["questions"][0]["question"] = "잘린 응답에서 버려야 하는 질문"
    client = SequencedClient([truncated, *batches], ["max_tokens", "tool_use", "tool_use", "tool_use"])
    result = await generator(client).execute(request(), job_id="truncated-job")

    assert len(client.calls) == 4
    assert all(q.question != "잘린 응답에서 버려야 하는 질문" for q in result)
    retry = client.calls[1]["messages"]
    assert len(retry) == 1
    assert retry[0]["role"] == "user"
    assert "토큰 한도로 잘렸다" in retry[0]["content"]
    assert "job_id=truncated-job" in caplog.text
    assert "max_tokens=12288 output_tokens=10" in caplog.text


async def test_repeated_truncation_fails_without_starting_the_next_set() -> None:
    batch = solo_submissions()[0]
    client = SequencedClient([batch, batch], ["max_tokens", "max_tokens"])
    with pytest.raises(PipelineError, match="구성 규칙"):
        await generator(client).execute(request())
    assert len(client.calls) == 2


@pytest.mark.parametrize("defect", ["missing", "extra", "wrong_set", "missing_category", "unknown_evidence"])
async def test_batch_rules_are_enforced_before_acceptance(defect: str, caplog: pytest.LogCaptureFixture) -> None:
    batches = solo_submissions()
    broken = deepcopy(batches[0])
    questions = broken["questions"]
    if defect == "missing":
        questions.pop()
    elif defect == "extra":
        questions.append(deepcopy(questions[0]))
    elif defect == "wrong_set":
        questions[0]["set_no"] = 2
    elif defect == "missing_category":
        questions[0]["category"] = questions[1]["category"]
    else:
        questions[0]["based_on"] = ["https://example.com/source?token=secret-value"]
    client = SequencedClient([broken, *batches])
    result = await generator(client).execute(request())

    assert len(result) == 15
    assert len(client.calls) == 4
    assert "violation_count=" in caplog.text
    assert "secret-value" not in caplog.text
    if defect == "unknown_evidence":
        assert "목록에 없는 경로" in caplog.text
        assert "[redacted]" in caplog.text


class FailingClient(SequencedClient):
    async def call(self, **kwargs: Any) -> NoReturn:
        raise AnthropicTextClientError("DNS failure", kind="transient")


async def test_client_error_remains_a_failure_with_job_context(caplog: pytest.LogCaptureFixture) -> None:
    with pytest.raises(PipelineError, match="생성에 실패"):
        await generator(FailingClient([])).execute(request(), job_id="dns-job")
    assert "job_id=dns-job kind=transient" in caplog.text
