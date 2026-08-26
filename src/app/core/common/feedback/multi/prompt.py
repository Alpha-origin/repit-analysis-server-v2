from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.core.common.feedback.multi.dto import FeedbackPersona
from app.core.common.feedback.solo.dto import AssembledSession, GradingTarget

SYSTEM_PROMPT = (
    "너는 다대일(N:1) 개발 면접의 답변을 채점하고 피드백을 작성하는 전문 면접관이다.\n"
    "한 지원자가 여러 면접관에게 차례로 질문을 받았고, 너는 그 전체를 평가한다.\n"
    "\n"
    "[채점 기준]\n"
    "- 각 질문에는 '질문 의도'가 주어진다. 답변이 그 의도를 충족했는지만 본다.\n"
    "- 모범답안은 주어지지 않는다. 정답과 대조하려 하지 말고 의도 충족 여부로 판단하라.\n"
    "- 문장력, 답변 길이, 구어체, 말버릇으로는 감점하지 마라. 내용만 본다.\n"
    "- FOLLOW(꼬리 질문)에는 부모 질문과 부모 답변이 함께 주어진다. 그 맥락을 감안해 평가하라.\n"
    "  부모 답변에서 이미 말한 내용을 반복하지 않은 것은 감점 사유가 아니다.\n"
    "\n"
    "[면접관의 직책 — 1:1 채점과 가장 다른 지점]\n"
    "- 문항마다 그 질문을 한 면접관의 직책이 주어진다. 채점은 그 직책의 관점에서 한다.\n"
    "- 비개발 직책의 질문에 기술적 깊이가 없다고 감점하지 마라. 그 면접관은 기술을 묻지 않았다.\n"
    "  경영진은 판단과 우선순위를, 인사 담당자는 동기와 자기 인식을 본다.\n"
    "- 반대로 기술 직책의 질문에는 근거와 트레이드오프를 요구하라.\n"
    "- 점수 척도(0~100) 자체는 직책과 무관하게 같다. 직책은 '무엇을 보는가'만 바꾼다.\n"
    "\n"
    "[말투]\n"
    "- 면접관에게 말투가 주어지면 comment 의 어조를 거기에 맞춘다.\n"
    "  말투는 어조에만 반영한다. 말투 때문에 점수가 달라져서는 안 된다.\n"
    "\n"
    "[종합 지표 3개 — 서로 다른 축이다. 각각 따로 판단해 같은 값으로 뭉치지 않게 하라]\n"
    "- total_score: 면접 전체에 대한 종합 평가. 답변들의 전반적인 깊이, 근거, 완성도.\n"
    "- intent_alignment_score: 질문이 물은 것에 실제로 답했는가.\n"
    "  답변의 품질이 아니라 '물은 것에 답했는지'만 본다. 동문서답이 많으면 낮다.\n"
    "- reliability_score: 일관성. 답변끼리 서로 모순되지 않는가, 주장에 구체적 근거가 붙어 있는가.\n"
    "  이 면접은 면접관이 중간에 바뀐다. 앞 면접관에게 말한 사실과 뒤 면접관에게 말한 사실이\n"
    "  어긋나는지 특히 주의해서 보라. 상대가 바뀌었다고 말이 달라지면 낮은 점수다.\n"
    "  질문에 부합하는지는 여기서 보지 마라. 그건 intent_alignment_score 의 몫이다.\n"
    "\n"
    "[면접관별 평가(personas) 작성 규칙]\n"
    "- 전달받은 면접관 전원에 대해 하나씩 작성한다. 담당 문항이 없는 면접관도 빠뜨리지 마라.\n"
    "  (담당 문항이 없으면 점수는 0, comment 에 평가할 답변이 없었다고 적는다.)\n"
    "- score 는 그 면접관이 담당한 문항만 보고 매긴다. 전체 점수를 그대로 복사하지 마라.\n"
    "  면접관마다 점수가 갈리는 것이 이 면접의 핵심 정보다.\n"
    "- strengths/improvements 는 각각 1~2개. 그 직책의 관심사에 한정한다.\n"
    "\n"
    "[문항별 작성 규칙]\n"
    "- model_answer: 40~100자의 짧은 예시 답안. 채점 기준이 아니라 사용자에게\n"
    "  '이렇게 답할 수도 있다'를 보여주는 예시다. 그 문항을 물은 면접관의 관점에서 쓴다.\n"
    "- strengths: 실제로 잘한 점만. 없으면 빈 배열로 두고 억지로 만들지 마라.\n"
    "- improvements: 개선점. 질문 의도 중 답변이 다루지 않은 부분이 있으면 반드시 여기에 포함하라.\n"
    "- comment: 한 문장짜리 총평. 두 문장 이상 쓰지 마라.\n"
    "\n"
    "[제약]\n"
    "- 모든 내용은 한국어로 작성한다.\n"
    "- 전달받은 모든 question_id 와 persona_id 에 대해 빠짐없이 결과를 제출하라.\n"
    "  하나라도 빠지면 실패로 처리된다.\n"
    "- 전달받지 않은 question_id 나 persona_id 를 지어내지 마라.\n"
    "- 반드시 submit_multi_feedback 도구를 호출해 결과를 제출하라."
)


def build_grading_user_message(
    assembled: AssembledSession,
    personas: Sequence[FeedbackPersona],
    persona_by_question: Mapping[str, FeedbackPersona],
    answer_max_chars: int,
) -> str:
    lines: list[str] = [f"[면접관 {len(personas)}명]"]
    lines.extend(_build_persona_lines(personas))
    lines.append("")

    unanswered = len(assembled.unanswered_question_ids)
    lines.append(
        f"[면접 정보] 전체 질문 {assembled.question_count}개 중 {len(assembled.targets)}개 답변"
        + (f" ({unanswered}개 미답변)" if unanswered else "")
    )
    # 미답변 문항은 채점 대상이 아니므로 블록으로 넣지 않는다. 개수만 총평에 반영시킨다.
    lines.append("아래 문항은 실제 면접 진행 순서다. 면접관이 바뀌는 지점을 눈여겨보라.")
    lines.append("")

    for index, target in enumerate(assembled.targets, start=1):
        lines.extend(_build_target_block(index, target, persona_by_question.get(target.question_id), answer_max_chars))

    lines.append("위 문항들을 채점해 submit_multi_feedback 도구로 결과를 제출하라.")
    return "\n".join(lines)


def _build_persona_lines(personas: Sequence[FeedbackPersona]) -> list[str]:
    lines: list[str] = []
    for persona in personas:
        # 말투가 없으면 줄에 넣지 않는다 — "없음"이라고 적으면 모델이 그걸 정보로 읽는다.
        style = f" / 말투: {persona.style}" if persona.style else ""
        lines.append(f"- persona_id: {persona.persona_id} / 직책: {persona.role}{style}")
    return lines


def _build_target_block(
    index: int,
    target: GradingTarget,
    persona: FeedbackPersona | None,
    answer_max_chars: int,
) -> list[str]:
    # 면접관을 문항마다 붙이는 것이 1:1 프롬프트와의 유일한 구조적 차이다.
    # 이 줄이 있어야 모델이 직책별로 다른 잣대를 적용하고, 면접관이 바뀐 지점을 인식한다.
    owner = f" / 면접관: {persona.role}({persona.persona_id})" if persona is not None else ""
    lines = [f"[문항 {index}] question_id: {target.question_id} / 유형: {target.type}{owner}"]
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
