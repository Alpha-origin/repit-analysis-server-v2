"""채점 응답이 검증을 못 넘으면 같은 입력으로 한 번만 다시 부른다 (이슈 #46)."""

import copy
import logging
from typing import Any

import pytest

from app.core.commands.dispatch_feedback_multi import DispatchFeedbackMulti
from app.core.commands.dispatch_feedback_solo import DispatchFeedbackSolo
from app.core.common.feedback.multi.answer_grading import MultiAnswerGrading
from app.core.common.feedback.multi.dto import FeedbackMultiRequest
from app.core.common.feedback.solo.answer_assembly import AnswerAssembly
from app.core.common.feedback.solo.answer_grading import AnswerGrading
from app.core.common.feedback.solo.dto import FeedbackSoloRequest
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


async def _dispatch(mode: str, client: SequencedGradingClient) -> dict[str, Any]:
    webhook = RecordingWebhook()
    body = feedback_body()
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
