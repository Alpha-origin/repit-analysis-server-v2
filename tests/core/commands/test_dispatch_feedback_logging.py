from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, cast

from _pytest.logging import LogCaptureFixture

from app.core.commands.dispatch_feedback_multi import DispatchFeedbackMulti
from app.core.commands.dispatch_feedback_solo import DispatchFeedbackSolo
from app.core.common.feedback.multi.answer_grading import MultiAnswerGrading
from app.core.common.feedback.multi.dto import (
    FeedbackMultiRequest,
    FeedbackPersona,
    MultiFeedbackQuestion,
)
from app.core.common.feedback.solo.answer_assembly import AnswerAssembly
from app.core.common.feedback.solo.answer_grading import AnswerGrading
from app.core.common.feedback.solo.dto import (
    AssembledSession,
    FeedbackAnswer,
    FeedbackQuestion,
    FeedbackSoloRequest,
)
from app.core.common.interview_qa.ports.webhook_client import WebhookClient


class _RecordingWebhook:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    async def send(self, url: str, payload: dict[str, Any]) -> bool:
        self.payloads.append(payload)
        return True


class _SoloGrading:
    async def execute(
        self,
        assembled: AssembledSession,
        persona_type: str | None,
    ) -> dict[str, Any]:
        return {
            "overall": _overall(),
            "feedbacks": {"q1": _answer_feedback()},
        }


class _MultiGrading:
    async def execute(
        self,
        assembled: AssembledSession,
        personas: tuple[FeedbackPersona, ...],
        persona_by_question: dict[str, FeedbackPersona],
    ) -> dict[str, Any]:
        return {
            "overall": _overall(),
            "personas": {
                "p1": {
                    "score": 82,
                    "comment": "기술 선택 근거가 명확합니다.",
                    "strengths": ["구체적인 설명"],
                    "improvements": ["대안 비교 보강"],
                }
            },
            "feedbacks": {"q1": _answer_feedback()},
        }


def _overall() -> dict[str, Any]:
    return {
        "total_score": 80,
        "intent_alignment_score": 78,
        "reliability_score": 84,
        "summary": "질문 의도에 맞게 답했습니다.",
        "strengths": ["근거가 명확함"],
        "improvements": ["대안 비교가 필요함"],
    }


def _answer_feedback() -> dict[str, Any]:
    return {
        "model_answer": "트래픽 특성과 만료 정책을 기준으로 Redis를 선택했습니다.",
        "strengths": ["선택 근거 제시"],
        "improvements": ["장애 대응 설명"],
        "comment": "핵심 근거를 잘 설명했습니다.",
    }


def _question() -> FeedbackQuestion:
    return FeedbackQuestion(
        question_id="q1",
        type="ORIGINAL",
        intention="캐시 선택 근거 확인",
        content="왜 Redis를 선택했나요?",
        created_at=datetime(2026, 8, 27, 10),
    )


def _answer(content: str = "응답 속도와 TTL 지원을 고려했습니다.") -> FeedbackAnswer:
    return FeedbackAnswer(
        answer_id="a1",
        question_id="q1",
        content=content,
        created_at=datetime(2026, 8, 27, 10, 1),
    )


async def test_solo_logs_every_stage_and_final_feedback(caplog: LogCaptureFixture) -> None:
    webhook = _RecordingWebhook()
    dispatcher = DispatchFeedbackSolo(
        webhook=cast("WebhookClient", webhook),
        answer_assembly=AnswerAssembly(),
        answer_grading=cast("AnswerGrading", _SoloGrading()),
        frequent_word_top_n=10,
        frequent_word_min_count=2,
    )
    request = FeedbackSoloRequest(
        session_id="session-1",
        interview_id="interview-1",
        user_id="user-1",
        questions=(_question(),),
        answers=(_answer(),),
        callback_url="https://example.com/callback",
    )
    caplog.set_level(logging.INFO, logger="app.core.commands.dispatch_feedback_solo")

    await dispatcher.execute("job-1", request)

    stage_records = [record for record in caplog.records if record.msg == "feedback_solo.dispatch.stage.done"]
    assert [record.__dict__["stage"] for record in stage_records] == [
        "answer_assembly",
        "llm_grading",
        "result_building",
    ]
    final_record = next(record for record in caplog.records if record.msg == "feedback_solo.dispatch.final_feedback")
    feedback = final_record.__dict__["feedback"]
    assert feedback["overall"]["totalScore"] == 80
    assert feedback["feedbacks"][0]["comment"] == "핵심 근거를 잘 설명했습니다."
    assert "userAnswer" not in feedback["feedbacks"][0]
    assert webhook.payloads[0]["status"] == "succeeded"


async def test_solo_logs_failed_stage_and_sends_failure_callback(caplog: LogCaptureFixture) -> None:
    webhook = _RecordingWebhook()
    dispatcher = DispatchFeedbackSolo(
        webhook=cast("WebhookClient", webhook),
        answer_assembly=AnswerAssembly(),
        answer_grading=cast("AnswerGrading", _SoloGrading()),
        frequent_word_top_n=10,
        frequent_word_min_count=2,
    )
    request = FeedbackSoloRequest(
        session_id="session-1",
        interview_id="interview-1",
        user_id="user-1",
        questions=(_question(),),
        answers=(_answer("   "),),
        callback_url="https://example.com/callback",
    )
    caplog.set_level(logging.INFO, logger="app.core.commands.dispatch_feedback_solo")

    await dispatcher.execute("job-1", request)

    failed_record = next(record for record in caplog.records if record.msg == "feedback_solo.dispatch.stage.failed")
    assert failed_record.__dict__["stage"] == "answer_assembly"
    assert failed_record.__dict__["status_code"] == 422
    assert webhook.payloads[0]["status"] == "failed"
    assert not any(record.msg == "feedback_solo.dispatch.final_feedback" for record in caplog.records)


async def test_multi_logs_persona_stage_and_final_feedback(caplog: LogCaptureFixture) -> None:
    webhook = _RecordingWebhook()
    dispatcher = DispatchFeedbackMulti(
        webhook=cast("WebhookClient", webhook),
        answer_assembly=AnswerAssembly(),
        answer_grading=cast("MultiAnswerGrading", _MultiGrading()),
        frequent_word_top_n=10,
        frequent_word_min_count=2,
    )
    base_question = _question()
    request = FeedbackMultiRequest(
        session_id="session-1",
        interview_id="interview-1",
        user_id="user-1",
        personas=(FeedbackPersona(persona_id="p1", role="TECH"),),
        questions=(
            MultiFeedbackQuestion(
                **base_question.model_dump(),
                persona_id="p1",
            ),
        ),
        answers=(_answer(),),
        callback_url="https://example.com/callback",
    )
    caplog.set_level(logging.INFO, logger="app.core.commands.dispatch_feedback_multi")

    await dispatcher.execute("job-1", request)

    stage_records = [record for record in caplog.records if record.msg == "feedback_multi.dispatch.stage.done"]
    assert [record.__dict__["stage"] for record in stage_records] == [
        "answer_assembly",
        "persona_mapping",
        "llm_grading",
        "result_building",
    ]
    final_record = next(record for record in caplog.records if record.msg == "feedback_multi.dispatch.final_feedback")
    feedback = final_record.__dict__["feedback"]
    assert feedback["personas"][0]["personaId"] == "p1"
    assert feedback["feedbacks"][0]["personaId"] == "p1"
    assert "userAnswer" not in feedback["feedbacks"][0]
    assert webhook.payloads[0]["status"] == "succeeded"
