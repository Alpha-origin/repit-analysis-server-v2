import json
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
from app.inbound.http.interview_feedback.multi.dto import FeedbackMultiHttpRequest
from app.inbound.http.interview_feedback.solo.dto import FeedbackRequest
from app.main.run import _setup_logging
from tests.multi_helpers import RecordingWebhook, StubTextClient, feedback_body, feedback_output


async def _dispatch(mode: str, output: dict[str, Any]) -> dict[str, Any]:
    webhook = RecordingWebhook()
    if mode == "solo":
        client = StubTextClient({"submit_feedback": output})
        request = FeedbackSoloRequest.model_validate(
            FeedbackRequest.model_validate(feedback_body()).model_dump(mode="json")
        )
        solo = DispatchFeedbackSolo(webhook, AnswerAssembly(), AnswerGrading(client, "stub", 4096, 3000), 10, 2)
        await solo.execute("job-1", request)
    else:
        client = StubTextClient({"submit_multi_feedback": output})
        multi_request = FeedbackMultiRequest.model_validate(
            FeedbackMultiHttpRequest.model_validate(feedback_body()).model_dump(mode="json")
        )
        multi = DispatchFeedbackMulti(webhook, AnswerAssembly(), MultiAnswerGrading(client, "stub", 4096, 3000), 10, 2)
        await multi.execute("job-1", multi_request)
    return webhook.payloads[0]


@pytest.mark.parametrize("mode", ["solo", "multi"])
@pytest.mark.parametrize("missing", ["summary", "strengths", "improvements", "all"])
async def test_missing_overall_text_produces_failure_callback(mode: str, missing: str) -> None:
    output = feedback_output()
    if missing == "all":
        output["overall"] = {}
    else:
        del output["overall"][missing]

    payload = await _dispatch(mode, output)

    assert payload["status"] == "failed"
    assert payload["error"] == {"statusCode": 500, "message": "피드백 결과가 형식을 충족하지 못했습니다."}
    assert "result" not in payload


@pytest.mark.parametrize("mode", ["solo", "multi"])
async def test_scoring_evidence_is_rendered_in_configured_log_format(
    mode: str, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    config: dict[str, Any] = {}
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: config.update(kwargs))
    _setup_logging("INFO")
    output = feedback_output()
    # 점수와 텍스트가 정상적으로 유지되고, 빈 배열은 유효한 값으로 인정되는지도 확인한다.
    output["overall"]["total_score"] = 0
    with caplog.at_level(logging.INFO):
        payload = await _dispatch(mode, output)

    assert payload["status"] == "succeeded"
    overall = payload["result"]["overall"]
    assert overall["summary"] == "총평"
    assert overall["strengths"] == []
    assert overall["improvements"] == []
    assert overall["totalScore"] == 81
    assert "scoreBreakdown" not in overall
    record = next(record for record in caplog.records if record.msg.startswith(f"feedback_{mode}.dispatch.graded"))
    rendered = logging.Formatter(config["format"]).format(record)
    evidence = json.loads(rendered.split("scoring=", 1)[1])
    assert evidence == {
        "job_id": "job-1",
        "scoring_version": "axis-v1",
        "axis_scores": {"intent": 100, "depth": 75, "specificity": 50, "accuracy": 100},
        "weights": {"intent": 35, "depth": 25, "specificity": 25, "accuracy": 15},
        "consistency": None,
        "question_levels": {"q-1": {"intent": 4, "depth": 3, "specificity": 2, "accuracy": 4}},
    }
    assert "성능 요구 때문에 선택했습니다" not in rendered
