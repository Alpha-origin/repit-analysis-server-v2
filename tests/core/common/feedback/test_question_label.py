import logging
from typing import Any

import pytest

from app.core.common.company.fit.evaluate import CompanyFitEvaluation
from app.core.common.company.fit.prompt import (
    SYSTEM_PROMPT as FIT_PROMPT,
    build_fit_user_message,
)
from app.core.common.feedback.multi.answer_grading import _parse_submission as parse_multi
from app.core.common.feedback.multi.dto import FeedbackPersona
from app.core.common.feedback.multi.prompt import (
    SYSTEM_PROMPT as MULTI_PROMPT,
    build_grading_user_message as build_multi_message,
)
from app.core.common.feedback.question_label import out_of_range_labels
from app.core.common.feedback.solo.answer_grading import _parse_submission as parse_solo
from app.core.common.feedback.solo.dto import AssembledSession, GradingTarget
from app.core.common.feedback.solo.prompt import (
    SYSTEM_PROMPT as SOLO_PROMPT,
    build_grading_user_message as build_solo_message,
)
from app.main.run import _setup_logging
from tests.company_helpers import RecordingCompanyClient, company_context, fit_output

_RULE = "문장에서 문항을 가리킬 때는 블록 번호(Q1, Q2 …)를 쓴다."
_CHECK = "문장에서 문항을 블록 번호(Q1, Q2 …)로 가리키고 question_id 값을 쓰지 않았는가?"
_ANSWER = "대기 시간을 줄이려고 비동기로 바꿨고 동기 방식보다 빨랐습니다."


def _targets() -> tuple[GradingTarget, ...]:
    # DB question_id 는 21부터지만 블록 번호는 채점 대상 순서 1부터다.
    return tuple(
        GradingTarget(question_id=str(qid), type="ORIGINAL", intention="선택 근거", content="왜?", answer=_ANSWER)
        for qid in (21, 22)
    )


def _assembled() -> AssembledSession:
    return AssembledSession(targets=_targets(), unanswered_question_ids=(), question_count=2)


def test_solo_blocks_use_q_number_and_rule_is_in_system_prompt() -> None:
    message = build_solo_message(_assembled(), None, 3000)

    assert "[Q1] question_id: 21 / 유형: ORIGINAL" in message
    assert "[Q2] question_id: 22 / 유형: ORIGINAL" in message
    assert "[문항 " not in message
    assert _RULE in SOLO_PROMPT
    assert "question_id 값은 도구의 question_id 필드에만 쓰고 문장에 쓰지 않는다." in SOLO_PROMPT
    assert f"7. {_CHECK}" in SOLO_PROMPT


def test_multi_blocks_use_q_number_and_rule_is_in_system_prompt() -> None:
    persona = FeedbackPersona(persona_id="p-0", role="TECH")
    message = build_multi_message(_assembled(), (persona,), {"21": persona, "22": persona}, 3000)

    assert "[Q1] question_id: 21 / 유형: ORIGINAL / 면접관: TECH(p-0)" in message
    assert "[Q2] question_id: 22 / 유형: ORIGINAL / 면접관: TECH(p-0)" in message
    assert "[문항 " not in message
    assert _RULE in MULTI_PROMPT
    assert "question_id 값은 도구의 question_id 필드에만 쓰고 문장에 쓰지 않는다." in MULTI_PROMPT
    assert f"8. {_CHECK}" in MULTI_PROMPT


def test_company_fit_blocks_use_q_number_and_rule_is_in_system_prompt() -> None:
    message = build_fit_user_message(_targets(), company_context(), None, 3000)

    assert "[Q1] question_id: 21" in message
    assert "[Q2] question_id: 22" in message
    assert "[문항 " not in message
    assert _RULE in FIT_PROMPT
    assert "question_id 값은 도구의 question_ids 필드에만 쓰고 문장에 쓰지 않는다." in FIT_PROMPT


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("정당화함 (Q21)", ["Q21"]),
        ("Q22: 근거 부족, Q22에서도 반복", ["Q22"]),
        ("Q1과 Q2에서 근거를 들었다", []),
        ("Q3 이후 설명", ["Q3"]),
        ("SQ21, Q21a, Q&A 는 문항 번호가 아니다", []),
    ],
)
def test_out_of_range_labels_only_flags_numbers_above_target_count(text: str, expected: list[str]) -> None:
    assert out_of_range_labels([text, None, ["목록 안 문장"]], 2) == expected


def test_out_of_range_labels_reads_string_lists() -> None:
    assert out_of_range_labels([["Q1 강점", "Q25 개선"]], 5) == ["Q25"]


def _feedback_entry(question_id: str, comment: str = "평가") -> dict[str, Any]:
    return {
        "question_id": question_id,
        "model_answer": "예시",
        "strengths": [],
        "improvements": [],
        "comment": comment,
        "scores": {"intent": 4, "specificity": 2, "accuracy": 4},
        "depth_evidence": {
            "reason": "대기 시간을 줄이려고",
            "alternatives": "동기 방식보다 빨랐습니다.",
            "limitation": "",
            "generic_reason": "",
        },
    }


def _tool(name: str, value: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"type": "tool_use", "name": name, "input": value}]


_SOURCES = {"21": (_ANSWER,), "22": (_ANSWER,)}


def _label_records(caplog: pytest.LogCaptureFixture, event: str) -> list[Any]:
    return [record for record in caplog.records if record.getMessage().startswith(f"{event} ")]


def test_solo_warns_on_question_id_label_without_changing_text(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    config: dict[str, Any] = {}
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: config.update(kwargs))
    _setup_logging("INFO")
    overall = {"summary": "총평", "strengths": ["정당화함 (Q21)"], "improvements": ["Q22: 근거 보완"]}
    feedbacks = [_feedback_entry("21"), _feedback_entry("22", comment="Q2 답변은 좋았다")]

    result = parse_solo(_tool("submit_feedback", {"overall": overall, "feedbacks": feedbacks}), ("21", "22"), _SOURCES)

    (record,) = _label_records(caplog, "feedback_solo.grading.question_id_label")
    assert record.levelname == "WARNING"
    assert record.__dict__["labels"] == ["Q21", "Q22"]
    assert record.__dict__["target_count"] == 2
    # 운영 로그 포맷(extra 미출력)에서도 번호가 보여야 한다.
    rendered = logging.Formatter(config["format"]).format(record)
    assert "feedback_solo.grading.question_id_label labels=Q21,Q22 target_count=2" in rendered
    # 재시도·치환 없이 그대로 쓴다.
    assert result["overall"]["strengths"] == ["정당화함 (Q21)"]


def test_solo_does_not_warn_for_block_numbers(caplog: pytest.LogCaptureFixture) -> None:
    overall = {"summary": "Q1과 Q2 모두", "strengths": ["Q1 근거"], "improvements": []}
    feedbacks = [_feedback_entry("21"), _feedback_entry("22")]

    parse_solo(_tool("submit_feedback", {"overall": overall, "feedbacks": feedbacks}), ("21", "22"), _SOURCES)

    assert _label_records(caplog, "feedback_solo.grading.question_id_label") == []


def test_multi_warns_on_question_id_label_in_persona_text(caplog: pytest.LogCaptureFixture) -> None:
    value = {
        "overall": {"summary": "총평", "strengths": [], "improvements": []},
        "feedbacks": [_feedback_entry("21"), _feedback_entry("22")],
        "personas": [{"persona_id": "p-0", "comment": "Q21에서 근거가 좋았다", "strengths": [], "improvements": []}],
    }

    result = parse_multi(_tool("submit_multi_feedback", value), ("21", "22"), ("p-0",), _SOURCES)

    (record,) = _label_records(caplog, "feedback_multi.grading.question_id_label")
    assert record.__dict__["labels"] == ["Q21"]
    assert record.__dict__["target_count"] == 2
    assert result["personas"]["p-0"]["comment"] == "Q21에서 근거가 좋았다"


async def test_company_fit_warns_on_question_id_label(caplog: pytest.LogCaptureFixture) -> None:
    client = RecordingCompanyClient()
    raw = fit_output()
    raw["comment"] = "Q21 답변에서 오너십이 드러났습니다"
    client.outputs["submit_company_fit"] = raw
    target = GradingTarget(question_id="q-1", type="ORIGINAL", intention="선택 근거", content="왜?", answer=_ANSWER)

    result = await CompanyFitEvaluation(client, "stub", 1024, 0, 3000).execute((target,), company_context(), None)

    assert result is not None
    assert result.comment == "Q21 답변에서 오너십이 드러났습니다"
    (record,) = _label_records(caplog, "company_fit.question_id_label")
    assert record.__dict__["labels"] == ["Q21"]
    assert record.__dict__["target_count"] == 1
