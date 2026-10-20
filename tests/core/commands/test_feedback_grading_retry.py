"""채점 응답이 검증을 못 넘으면 같은 입력으로 한 번만 다시 부른다 (이슈 #46)."""

import copy
import logging
from typing import Any

import pytest

from app.core.commands.dispatch_feedback_multi import DispatchFeedbackMulti
from app.core.commands.dispatch_feedback_solo import DispatchFeedbackSolo
from app.core.common.feedback.grading_retry import log_dropped
from app.core.common.feedback.multi.answer_grading import MultiAnswerGrading
from app.core.common.feedback.multi.dto import FeedbackMultiRequest
from app.core.common.feedback.multi.prompt import (
    SYSTEM_PROMPT as MULTI_SYSTEM_PROMPT,
    system_prompt_for_job as multi_system_prompt,
)
from app.core.common.feedback.multi.tools import feedback_tool_for_job as multi_feedback_tool
from app.core.common.feedback.solo.answer_assembly import AnswerAssembly
from app.core.common.feedback.solo.answer_grading import AnswerGrading
from app.core.common.feedback.solo.dto import FeedbackSoloRequest
from app.core.common.feedback.solo.prompt import (
    SYSTEM_PROMPT as SOLO_SYSTEM_PROMPT,
    system_prompt_for_job as solo_system_prompt,
)
from app.core.common.feedback.solo.tools import feedback_tool_for_job as solo_feedback_tool
from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicCallResult, AnthropicTextClientError
from app.inbound.http.interview_feedback.multi.dto import FeedbackMultiHttpRequest
from app.inbound.http.interview_feedback.solo.dto import FeedbackRequest
from tests.multi_helpers import RecordingWebhook, feedback_body, feedback_output

# 원문에 없는 표현으로 바꾼 인용. 문항이 탈락한다.
_PARAPHRASED = "속도를 우선했습니다."


class SequencedGradingClient:
    """호출마다 준비한 응답을 차례로 돌려준다. 같은 입력으로 다시 불렀는지도 기록한다."""

    def __init__(self, outputs: list[dict[str, Any] | Exception], stop_reason: str = "tool_use") -> None:
        self.outputs = outputs
        self.stop_reason = stop_reason
        self.calls: list[dict[str, Any]] = []

    async def call(self, **kwargs: Any) -> AnthropicCallResult:
        self.calls.append(kwargs)
        output = self.outputs[len(self.calls) - 1]
        if isinstance(output, Exception):
            raise output
        name = kwargs["tools"][0]["name"]
        return AnthropicCallResult(
            content_blocks=[{"type": "tool_use", "name": name, "input": output}],
            input_tokens=10,
            output_tokens=10,
            stop_reason=self.stop_reason,
        )


def _bad_output() -> dict[str, Any]:
    output = copy.deepcopy(feedback_output())
    output["feedbacks"][0]["depth_evidence"]["reason"] = _PARAPHRASED
    return output


async def _dispatch(mode: str, client: SequencedGradingClient, **body_changes: Any) -> dict[str, Any]:
    webhook = RecordingWebhook()
    body = {**feedback_body(), **body_changes}
    if mode == "solo":
        request = FeedbackSoloRequest.model_validate(FeedbackRequest.model_validate(body).model_dump(mode="json"))
        solo = DispatchFeedbackSolo(webhook, AnswerAssembly(), AnswerGrading(client, "stub", 4096, 3000), 10, 2)
        await solo.execute("job-1", request)
    else:
        multi_request = FeedbackMultiRequest.model_validate(
            FeedbackMultiHttpRequest.model_validate(body).model_dump(mode="json")
        )
        multi = DispatchFeedbackMulti(webhook, AnswerAssembly(), MultiAnswerGrading(client, "stub", 4096, 3000), 10, 2)
        await multi.execute("job-1", multi_request)
    return webhook.payloads[0]


@pytest.mark.parametrize("mode", ["solo", "multi"])
async def test_failed_validation_is_retried_once_with_the_same_input(mode: str) -> None:
    client = SequencedGradingClient([_bad_output(), feedback_output()])

    payload = await _dispatch(mode, client)

    assert payload["status"] == "succeeded"
    assert len(client.calls) == 2
    assert client.calls[0] == client.calls[1]


@pytest.mark.parametrize("mode", ["solo", "multi"])
async def test_second_failure_still_fails_without_mixing_responses(mode: str) -> None:
    client = SequencedGradingClient([_bad_output(), _bad_output()])

    payload = await _dispatch(mode, client)

    assert payload["status"] == "failed"
    assert payload["error"]["statusCode"] == 500
    assert len(client.calls) == 2


@pytest.mark.parametrize("mode", ["solo", "multi"])
async def test_model_call_error_is_not_retried(mode: str) -> None:
    client = SequencedGradingClient([AnthropicTextClientError("down", kind="transient"), feedback_output()])

    payload = await _dispatch(mode, client)

    assert payload["status"] == "failed"
    assert payload["error"]["statusCode"] == 502
    assert len(client.calls) == 1


@pytest.mark.parametrize("mode", ["solo", "multi"])
async def test_truncated_response_is_not_retried(mode: str) -> None:
    client = SequencedGradingClient([_bad_output(), feedback_output()], stop_reason="max_tokens")

    payload = await _dispatch(mode, client)

    assert payload["status"] == "failed"
    assert len(client.calls) == 1


@pytest.mark.parametrize("mode", ["solo", "multi"])
async def test_missing_tool_output_is_not_retried(mode: str) -> None:
    client = SequencedGradingClient([{"overall": "깨짐"}, feedback_output()])

    payload = await _dispatch(mode, client)

    assert payload["status"] == "failed"
    assert len(client.calls) == 1


@pytest.mark.parametrize("mode", ["solo", "multi"])
async def test_drop_reason_is_logged_without_answer_text(mode: str, caplog: pytest.LogCaptureFixture) -> None:
    client = SequencedGradingClient([_bad_output(), feedback_output()])

    with caplog.at_level(logging.WARNING):
        await _dispatch(mode, client)

    messages = [record.getMessage() for record in caplog.records]
    dropped = [message for message in messages if f"feedback_{mode}.grading.dropped" in message]
    assert dropped == [f"feedback_{mode}.grading.dropped key=q-1 reason=evidence_mismatch:reason attempt=1"]
    assert any(f"feedback_{mode}.grading.retry attempt=1" in message for message in messages)
    assert not any(_PARAPHRASED in message or "성능 요구" in message for message in messages)


@pytest.mark.parametrize("mode", ["solo", "multi"])
async def test_unknown_id_from_model_is_logged_without_its_value(mode: str, caplog: pytest.LogCaptureFixture) -> None:
    leaked = "홍길동 010-1234-5678 제 답변은"
    bad = copy.deepcopy(feedback_output())
    bad["feedbacks"][0]["question_id"] = leaked
    client = SequencedGradingClient([bad, feedback_output()])

    with caplog.at_level(logging.WARNING):
        await _dispatch(mode, client)

    dropped = [record for record in caplog.records if f"feedback_{mode}.grading.dropped" in record.getMessage()]
    # MULTI 는 등급·인용을 먼저 검사하므로 같은 응답이 인용 불일치로 먼저 걸린다.
    reason = "unknown_question_id" if mode == "solo" else "evidence_mismatch:reason"
    assert [record.getMessage() for record in dropped] == [
        f"feedback_{mode}.grading.dropped key=<unknown str len={len(leaked)}> reason={reason} attempt=1"
    ]
    # 메시지와 extra 어디에도 모델이 보낸 값이 남지 않는다.
    assert not any(leaked in record.getMessage() or leaked in str(vars(record)) for record in caplog.records)


def test_dropped_log_keeps_only_numeric_score_values(caplog: pytest.LogCaptureFixture) -> None:
    leaked = "지원자가 말한 고객명 홍길동"
    with caplog.at_level(logging.WARNING):
        log_dropped(
            logging.getLogger("test"),
            "event",
            "q-1",
            "invalid_scores",
            1,
            {"accuracy": leaked, "logic": 2, "depth": None},
        )
    record = caplog.records[0]
    assert vars(record)["scores"] == {"accuracy": "<str>", "logic": 2, "depth": None}
    assert leaked not in str(vars(record))


@pytest.mark.parametrize("mode", ["solo", "multi"])
@pytest.mark.parametrize(("job_role", "job_title"), [("GEN_MARKETING", "퍼포먼스 마케터"), ("DATA_ANALYST", None)])
async def test_retry_keeps_job_specific_prompt_and_tool(mode: str, job_role: str, job_title: str | None) -> None:
    # 재시도 반복문(#46)과 직무별 채점(#52)이 만난 자리다. 한쪽만 남기면 이 검사가 실패해야 한다.
    client = SequencedGradingClient([_bad_output(), feedback_output()])
    changes: dict[str, Any] = {"jobRole": job_role} | ({"jobTitle": job_title} if job_title else {})

    payload = await _dispatch(mode, client, **changes)

    if mode == "solo":
        default, system, tool = SOLO_SYSTEM_PROMPT, solo_system_prompt, solo_feedback_tool
    else:
        default, system, tool = MULTI_SYSTEM_PROMPT, multi_system_prompt, multi_feedback_tool
    assert payload["status"] == "succeeded"
    assert len(client.calls) == 2
    for call in client.calls:
        assert call["system"] == system(job_role, job_title)
        assert call["system"] != default
        assert call["tools"] == [tool(job_role)]
