from __future__ import annotations

from app.core.common.feedback.solo.dto import AssembledSession, GradingTarget
from app.core.common.persona_guidance import build_persona_guidance

SYSTEM_PROMPT = (
    "너는 개발 면접 답변을 채점하고 피드백을 작성하는 전문 면접관이다.\n"
    "\n"
    "[채점 기준]\n"
    "- 각 질문에는 '질문 의도'가 주어진다. 답변이 그 의도를 충족했는지만 본다.\n"
    "- 모범답안은 주어지지 않는다. 정답과 대조하려 하지 말고 의도 충족 여부로 판단하라.\n"
    "- 문장력, 답변 길이, 구어체, 말버릇으로는 감점하지 마라. 내용만 본다.\n"
    "- FOLLOW(꼬리 질문)에는 부모 질문과 부모 답변이 함께 주어진다. 그 맥락을 감안해 평가하라.\n"
    "  부모 답변에서 이미 말한 내용을 반복하지 않은 것은 감점 사유가 아니다.\n"
    "\n"
    "[종합 지표 3개 — 서로 다른 축이다. 각각 따로 판단해 같은 값으로 뭉치지 않게 하라]\n"
    "- total_score: 면접 전체에 대한 종합 평가. 답변들의 전반적인 깊이, 근거, 완성도.\n"
    "- intent_alignment_score: 질문이 물은 것에 실제로 답했는가.\n"
    "  답변의 품질이 아니라 '물은 것에 답했는지'만 본다. 동문서답이 많으면 낮다.\n"
    "- reliability_score: 일관성. 답변끼리 서로 모순되지 않는가, 주장에 구체적 근거가 붙어 있는가.\n"
    "  질문에 부합하는지는 여기서 보지 마라. 그건 intent_alignment_score 의 몫이다.\n"
    "\n"
    "[문항별 작성 규칙]\n"
    "- model_answer: 40~100자의 짧은 예시 답안. 채점 기준이 아니라 사용자에게\n"
    "  '이렇게 답할 수도 있다'를 보여주는 예시다. 의도를 충족하는 방향을 간결히 제시하라.\n"
    "- strengths: 실제로 잘한 점만. 없으면 빈 배열로 두고 억지로 만들지 마라.\n"
    "- improvements: 개선점. 질문 의도 중 답변이 다루지 않은 부분이 있으면 반드시 여기에 포함하라.\n"
    "- comment: 한 문장짜리 총평. 두 문장 이상 쓰지 마라.\n"
    "\n"
    "[성향과 어조]\n"
    "- 성향 지침은 피드백을 바라보는 관점에만 반영한다. 채점 기준과 점수는 바꾸지 마라.\n"
    "- 어조 지침은 comment 와 summary 의 표현에만 반영한다. 채점 기준과 점수는 바꾸지 마라.\n"
    "\n"
    "[제약]\n"
    "- 모든 내용은 한국어로 작성한다.\n"
    "- 전달받은 모든 question_id 에 대해 빠짐없이 결과를 제출하라. 하나라도 빠지면 실패로 처리된다.\n"
    "- 전달받지 않은 question_id 를 지어내지 마라.\n"
    "- 반드시 submit_feedback 도구를 호출해 결과를 제출하라."
)


def build_grading_user_message(
    assembled: AssembledSession,
    persona_type: str | None,
    answer_max_chars: int,
    persona_tone: str | None = None,
) -> str:
    lines: list[str] = ["[면접 정보]"]
    lines.extend(build_persona_guidance(persona_type, persona_tone))
    unanswered = len(assembled.unanswered_question_ids)
    lines.append(
        f"전체 질문 {assembled.question_count}개 중 {len(assembled.targets)}개 답변"
        + (f" ({unanswered}개 미답변)" if unanswered else "")
    )
    # 미답변 문항은 채점 대상이 아니므로 블록으로 넣지 않는다. 개수만 총평에 반영시킨다.
    lines.append("")

    for index, target in enumerate(assembled.targets, start=1):
        lines.extend(_build_target_block(index, target, answer_max_chars))

    lines.append("위 문항들을 채점해 submit_feedback 도구로 결과를 제출하라.")
    return "\n".join(lines)


def _build_target_block(index: int, target: GradingTarget, answer_max_chars: int) -> list[str]:
    lines = [f"[문항 {index}] question_id: {target.question_id} / 유형: {target.type}"]
    if target.parent_question is not None:
        lines.append(f"부모 질문: {target.parent_question}")
        lines.append(f"부모 답변: {_truncate(target.parent_answer, answer_max_chars)}")
    lines.append(f"질문 의도: {target.intention}")
    lines.append(f"질문: {target.content}")
    lines.append(f"답변: {_truncate(target.answer, answer_max_chars)}")
    lines.append("")
    return lines


def _truncate(text: str | None, max_chars: int) -> str:
    # 답변 하나가 비정상적으로 길어 프롬프트를 잠식하는 것을 막는다.
    if text is None:
        return "(답변 없음)"
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...(이하 생략)"
