from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.commands.dispatch_feedback_multi import DispatchFeedbackMulti
from app.core.commands.dispatch_feedback_solo import DispatchFeedbackSolo
from app.core.common.feedback.competency.catalog import Competency
from app.core.common.feedback.multi.answer_grading import MultiAnswerGrading
from app.core.common.feedback.multi.dto import FeedbackMultiRequest
from app.core.common.feedback.solo.answer_assembly import AnswerAssembly
from app.core.common.feedback.solo.answer_grading import AnswerGrading
from app.core.common.feedback.solo.dto import FeedbackSoloRequest
from app.inbound.http.interview_feedback.multi.dto import FeedbackMultiHttpRequest
from app.inbound.http.interview_feedback.solo.dto import FeedbackRequest
from app.main.config import FeedbackScoringSettings
from tests.competency_helpers import output
from tests.core.commands.test_feedback_result_contract import _dispatch
from tests.multi_helpers import RecordingWebhook, StubTextClient, feedback_body, feedback_output


async def dispatch(mode: str, body: dict[str, Any], raw: dict[str, Any]) -> tuple[dict[str, Any], AsyncMock]:
    webhook = RecordingWebhook()
    name = "submit_feedback" if mode == "solo" else "submit_multi_feedback"
    client = StubTextClient({name: raw})
    spy = AsyncMock(wraps=client.call)
    client.call = spy  # type: ignore[method-assign]
    if mode == "solo":
        request = FeedbackSoloRequest.model_validate(body)
        stage = AnswerGrading(client, "stub", 12288, 3000, "competency-v1")
        await DispatchFeedbackSolo(webhook, AnswerAssembly(), stage, 10, 2).execute("job", request)
    else:
        request_multi = FeedbackMultiRequest.model_validate(body)
        multi_stage = MultiAnswerGrading(client, "stub", 14336, 3000, "competency-v1")
        await DispatchFeedbackMulti(webhook, AnswerAssembly(), multi_stage, 10, 2).execute("job", request_multi)
    return webhook.payloads[0], spy


@pytest.mark.parametrize("mode", ["solo", "multi"])
async def test_v1_callback_and_no_answer_persona(mode: str) -> None:
    body = feedback_body()
    # 코어 요청은 snake_case이고 실제 HTTP DTO의 alias 변환을 거친다.
    dto = FeedbackRequest if mode == "solo" else FeedbackMultiHttpRequest
    body["questions"][0]["category"] = "tech_choice"
    payload, spy = await dispatch(
        mode,
        dto.model_validate(body).model_dump(mode="json"),
        output(reason="성능 요구 때문에 선택했습니다", alternatives="동기 방식보다 대기 시간이 짧았습니다"),
    )
    assert payload["status"] == "succeeded"
    result = payload["result"]
    assert result["overall"]["totalScore"] == 75
    assert result["overall"]["reliabilityScore"] is None
    assert result["overall"]["scoreBreakdown"]["scoringVersion"] == "competency-v1"
    assert len(result["overall"]["scoreBreakdown"]["axes"]) == 5
    assert result["feedbacks"][0]["responseLevel"] == 4
    assert result["feedbacks"][0]["axisScores"] == {"PROBLEM_SOLVING": 75}
    assert result["feedbacks"][0]["competencies"][0]["evidence"][0]["slot"] == "reason"
    assert spy.await_count == 1
    if mode == "multi":
        assert result["personas"][0]["score"] == 75
        assert result["personas"][0]["scoreBreakdown"]["credibility"] is None
        assert result["personas"][1]["scoreBreakdown"] is None


@pytest.mark.parametrize("mode", ["solo", "multi"])
async def test_bad_primary_quote_retries_once_then_fails(mode: str) -> None:
    body = feedback_body()
    body["questions"][0]["category"] = "tech_choice"
    dto = FeedbackRequest if mode == "solo" else FeedbackMultiHttpRequest
    payload, spy = await dispatch(
        mode, dto.model_validate(body).model_dump(mode="json"), output(reason="존재하지 않는 답변")
    )
    assert payload["status"] == "failed"
    assert spy.await_count == 2


def test_default_scoring_version_remains_v4() -> None:
    assert FeedbackScoringSettings(_env_file=None).VERSION == "axis-v4"


@pytest.mark.parametrize("mode", ["solo", "multi"])
async def test_v4_callback_does_not_gain_v1_fields(mode: str) -> None:
    payload = await _dispatch(mode, feedback_output())
    assert payload["status"] == "succeeded"
    feedback = payload["result"]["feedbacks"][0]
    assert {"responseLevel", "axisScores", "competencies", "category"}.isdisjoint(feedback)


@pytest.mark.parametrize("response", [0, 1])
async def test_motivation_cannot_change_overall_score(response: int) -> None:
    body = feedback_body()
    body["questions"][0]["category"] = "motivation"
    raw = output(Competency.MOTIVATION)
    raw["feedbacks"][0]["response"] = response
    payload, _ = await dispatch("solo", FeedbackRequest.model_validate(body).model_dump(mode="json"), raw)
    assert payload["status"] == "succeeded"
    assert payload["result"]["overall"]["totalScore"] == 0
    assert payload["result"]["feedbacks"][0]["axisScores"] == {}
