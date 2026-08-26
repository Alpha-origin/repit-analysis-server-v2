from __future__ import annotations

import logging
from collections.abc import Sequence

from app.core.common.interview_qa.dto import ProjectSummary
from app.core.common.question_tailor.dto import OriginalQuestion
from app.core.common.question_tailor.multi.dto import TailorPersona

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "너는 개발자 모의면접에서 '비개발 직군 면접관'이 물을 질문을 만드는 역할이다.\n"
    "기술 질문은 이미 다른 면접관이 맡았다. 너는 그 외 직책의 질문만 만든다.\n"
    "\n"
    "[가장 중요한 것 — 직책의 색깔]\n"
    "- 면접관마다 직책이 주어진다. 그 직책이 실제로 궁금해할 것만 물어라.\n"
    "- 같은 프로젝트라도 인사 담당자와 경영진은 다른 지점을 본다. 두 사람의 질문이\n"
    "  비슷해지면 실패다. 면접관끼리 축이 겹치지 않게 하라.\n"
    "- 기술적 깊이를 파고들지 마라. 그건 기술 면접관의 몫이다. 참고용으로 주어진\n"
    "  기술 질문과 같은 것을 다시 묻지 마라.\n"
    "\n"
    "[근거 사용 — 강제하지 않는다]\n"
    "- 이것은 모의면접이다. 모든 질문이 포트폴리오·코드에 근거할 필요는 없다.\n"
    "  각 직책이 자기 특색대로 묻는 것이 우선이다.\n"
    "- 다만 지원자의 프로젝트를 최소한 한 번은 언급하라. 프로젝트 요약에 연결할 것이\n"
    "  있으면 그 기능·기술 이름을 질문에 담는다.\n"
    "- 어느 지원자에게 물어도 똑같은 교과서 질문은 금지다.\n"
    "  (예: '본인의 장단점은?', '10년 후 모습은?', '팀워크가 중요한 이유는?')\n"
    "- 프로젝트 요약에 없는 사실을 지어내지 마라. 팀 규모, 매출, 사용자 수처럼\n"
    "  주어지지 않은 수치를 전제로 묻지 마라.\n"
    "\n"
    "[확인하려는 것(expected_answer) 작성 규칙]\n"
    "- 정답을 적는 자리가 아니다. '좋은 답변에 담겨야 할 요소'를 적는다.\n"
    "- 반드시 그 면접관의 직책 관점에서 쓴다. 기술적 정확성으로 평가하지 마라.\n"
    "- 나중에 채점 기준으로 쓰이므로, 무엇을 보면 되는지 구체적으로 적어라.\n"
    "\n"
    "[문장 규칙]\n"
    "- 한국어 존댓말 질문으로 쓴다. 200자를 넘기지 마라.\n"
    "- 면접관에게 말투가 주어지면 그 말투로 쓰되, 묻는 내용은 말투와 무관하게 유지한다.\n"
    "- 질문만 쓴다. 머리말, 번호, 해설, 답변 힌트를 붙이지 마라.\n"
    "\n"
    "[제출]\n"
    "- 반드시 submit_generated_questions 도구를 호출해 결과를 제출하라.\n"
    "- 면접관마다 지정된 개수를 정확히 채워라. 모자라거나 넘치면 실패로 처리된다."
)

# 직책별 질문 축. 알려진 직책은 여기서 관점을 고정하고, 모르는 직책은 아래 기본 지침으로 간다.
# role 문자열은 호출자가 자유롭게 넣을 수 있으므로(새 직책 추가 시 422 를 피하려고)
# 대소문자를 무시하고 맞춘다.
_ROLE_GUIDANCE: dict[str, str] = {
    "hr": (
        "지원 동기, 문제를 직접 붙잡게 된 계기, 그 과정에서 배운 것, 자기 인식을 본다. "
        "팀 갈등이나 역할 분담처럼 프로젝트 요약에 근거가 없는 소재는 피한다."
    ),
    "ceo": (
        "사업적 판단을 본다. 왜 그 기능을 먼저 만들었는지, 사용자에게 어떤 가치를 준다고 "
        "보는지, 계속 키운다면 무엇을 할지. 매출·시장 규모처럼 주어지지 않은 수치는 묻지 않는다."
    ),
    "pm": ("기능의 우선순위와 사용자 관점을 본다. 요구사항을 어떻게 정리했고 무엇을 덜어냈는지."),
    "design": ("사용자 경험과 화면 흐름에 대한 판단을 본다."),
}

# 표기 규약이 아직 확정되지 않아 한글 표기도 같은 지침으로 잇는다.
# 규약이 정해지면 한쪽만 남기면 된다.
_ROLE_ALIASES: dict[str, str] = {
    "인사": "hr",
    "인사팀": "hr",
    "경영진": "ceo",
    "대표": "ceo",
    "기획": "pm",
    "기획자": "pm",
    "디자인": "design",
    "디자이너": "design",
}

_DEFAULT_ROLE_GUIDANCE = (
    "그 직책이 채용 면접에서 실제로 확인할 만한 것을 본다. "
    "기술 구현의 정확성이 아니라 직책 고유의 관심사로 질문을 만든다."
)


def build_generate_user_message(
    personas: Sequence[TailorPersona],
    project_summary: ProjectSummary,
    tech_questions: Sequence[OriginalQuestion],
    text_max_chars: int,
) -> str:
    lines: list[str] = ["[지원자 프로젝트 요약]"]
    lines.extend(_build_project_lines(project_summary, text_max_chars))
    lines.append("")

    lines.append("[기술 면접관이 이미 맡은 질문 — 참고용, 중복 회피에만 쓴다]")
    lines.extend(f"- {_truncate(question.question, text_max_chars)}" for question in tech_questions)
    lines.append("")

    total = sum(persona.question_count for persona in personas)
    lines.append(f"[질문을 만들 면접관 {len(personas)}명 — 총 {total}개]")
    lines.append("")
    for index, persona in enumerate(personas, start=1):
        lines.extend(_build_persona_block(index, persona))

    lines.append("각 면접관의 직책에 맞는 질문을 만들어 submit_generated_questions 도구로 제출하라.")
    return "\n".join(lines)


def _build_persona_block(index: int, persona: TailorPersona) -> list[str]:
    lines = [f"[면접관 {index}] 직책: {persona.role}"]
    if persona.style:
        # 말투는 어조에만 반영한다. 없으면 줄 자체를 넣지 않는다 — "없음"이라고 적으면 모델이 정보로 읽는다.
        lines.append(f"말투: {persona.style}")
    lines.append(f"관점: {_role_guidance(persona.role)}")
    lines.append(f"만들 질문 수: {persona.question_count}개 (persona_index 는 {index})")
    lines.append("")
    return lines


def _role_guidance(role: str) -> str:
    key = role.strip().lower()
    key = _ROLE_ALIASES.get(key, key)
    guidance = _ROLE_GUIDANCE.get(key)
    if guidance is None:
        # 폴백은 실패가 아니라 품질 저하라 조용히 넘어가면 눈치채기 어렵다.
        # 직책 색깔이 흐려진 질문이 나오면 이 로그부터 확인하면 된다.
        logger.warning("question_tailor_multi.prompt.unknown_role", extra={"role": role})
        return _DEFAULT_ROLE_GUIDANCE
    return guidance


def _build_project_lines(project_summary: ProjectSummary, text_max_chars: int) -> list[str]:
    lines = [_truncate(project_summary.overview, text_max_chars)]

    if project_summary.tech_stack:
        lines.append(f"기술 스택: {', '.join(project_summary.tech_stack)}")

    if project_summary.repositories:
        lines.append("구성:")
        lines.extend(
            f"- {repository.repo}({repository.role}): {_truncate(repository.description, text_max_chars)}"
            for repository in project_summary.repositories
        )

    if project_summary.core_features:
        # 핵심 기능이 비개발 질문의 주 재료다. 여기 있는 이름을 질문에 담게 한다.
        lines.append("핵심 기능:")
        lines.extend(
            f"- {feature.name}: {_truncate(feature.description, text_max_chars)}"
            for feature in project_summary.core_features
        )

    return lines


def _truncate(text: str, max_chars: int) -> str:
    # 요약 한 항목이 비정상적으로 길어 프롬프트를 잠식하는 것을 막는다.
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...(이하 생략)"
