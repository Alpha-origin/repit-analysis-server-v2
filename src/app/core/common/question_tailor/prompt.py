from __future__ import annotations

from collections.abc import Sequence

from app.core.common.persona_guidance import build_persona_guidance
from app.core.common.question_tailor.dto import CandidateProfile, OriginalQuestion

SYSTEM_PROMPT = (
    "너는 이미 만들어진 개발 면접 질문을, 지원자의 사전 정보에 맞게 다시 쓰는 역할이다.\n"
    "질문을 새로 만드는 것이 아니라 기존 질문의 '본문 표현'만 바꾼다.\n"
    "\n"
    "[가장 중요한 제약 — 검증 포인트 보존]\n"
    "- 각 원질문에는 '확인하려는 것'이 함께 주어진다. 재작성된 질문으로도 그것을 그대로\n"
    "  확인할 수 있어야 한다. 묻는 대상이 바뀌면 실패다.\n"
    "- 질문의 초점을 옮기거나, 더 쉬운/다른 주제로 바꿔치기하지 마라.\n"
    "- 한 문항을 두 개로 쪼개지 마라. 여러 문항을 합치지도 마라.\n"
    "- 질문 개수는 전달받은 그대로다. 추가도 삭제도 없다.\n"
    "\n"
    "[사전 정보 사용 범위]\n"
    "- 지원 직무: 그 직무의 관점에서 자연스럽게 들리도록 표현을 맞춘다.\n"
    "  단 원질문이 다루는 기술 영역 자체를 직무에 맞춰 바꾸지 마라.\n"
    "- 경력 수준: 질문의 깊이와 어휘를 조절한다. 신입에게는 용어를 풀어 쓰고,\n"
    "  시니어에게는 판단 근거와 트레이드오프를 묻는 방향으로 조인다.\n"
    "- 면접관 성향: 성향 지침에 따라 질문의 접근 방식을 조절하되, 확인하려는 내용은 바꾸지 마라.\n"
    "- 면접관 어조: 어조 지침에 맞춰 질문 표현을 조절한다. 성향과 어조 때문에 질문의\n"
    "  검증 포인트가 달라져서는 안 된다.\n"
    "- 사전 정보가 원질문과 어긋나면(예: 프론트엔드 지원자인데 DB 인덱스 질문) 억지로 맞추지 말고\n"
    "  원문에 가깝게 두어라. 어긋난 정보에 맞추려다 질문을 망치는 것이 최악이다.\n"
    "\n"
    "[사실 창작 금지]\n"
    "- 너에게는 지원자의 코드도 포트폴리오도 주어지지 않는다. 근거 파일 경로만 맥락으로 주어진다.\n"
    "- 원질문에 없는 기술 스택, 수치, 장애 상황, 구현 방식을 새로 지어내지 마라.\n"
    "- 원질문에 있는 고유명사(레포명, 기술명, 기능명)는 그대로 유지한다.\n"
    "\n"
    "[문장 규칙]\n"
    "- 한국어 존댓말 질문으로 쓴다.\n"
    "- 200자를 넘기지 마라. 원문보다 크게 길어지지 않게 한다.\n"
    "- 질문만 쓴다. 머리말('안녕하세요'), 번호, 해설, 답변 힌트를 붙이지 마라.\n"
    "\n"
    "[제출]\n"
    "- 반드시 submit_tailored_questions 도구를 호출해 결과를 제출하라.\n"
    "- 전달받은 모든 id 에 대해 빠짐없이, 전달받지 않은 id 는 만들지 말고 제출하라."
)


def build_rewrite_user_message(
    profile: CandidateProfile,
    questions: Sequence[OriginalQuestion],
    question_max_chars: int,
) -> str:
    # 요청 DTO 가 아니라 재료만 받는다. N:1 테일러도 같은 재작성 로직을 쓰기 때문이다.
    lines: list[str] = ["[지원자 사전 정보]"]
    lines.extend(_build_profile_lines(profile))
    lines.append("")
    lines.append(f"[원질문 {len(questions)}개]")
    lines.append("")

    for index, question in enumerate(questions, start=1):
        lines.extend(_build_question_block(index, question, question_max_chars))

    lines.append("위 질문들을 사전 정보에 맞게 다시 써서 submit_tailored_questions 도구로 제출하라.")
    return "\n".join(lines)


def _build_profile_lines(profile: CandidateProfile) -> list[str]:
    # 비어 있는 축은 아예 넣지 않는다. "없음" 이라고 적으면 모델이 그걸 정보로 읽는다.
    lines: list[str] = []
    if profile.job_role:
        lines.append(f"지원 직무: {profile.job_role}")
    if profile.experience_level:
        lines.append(f"경력 수준: {profile.experience_level}")
    lines.extend(build_persona_guidance(profile.persona_type, profile.persona_tone))
    return lines


def _build_question_block(index: int, question: OriginalQuestion, question_max_chars: int) -> list[str]:
    lines = [f"[문항 {index}] id: {question.id} / 카테고리: {question.category}"]
    lines.append(f"질문: {_truncate(question.question, question_max_chars)}")
    lines.append(f"확인하려는 것: {_truncate(question.expected_answer, question_max_chars)}")
    if question.based_on:
        # 파일 내용은 없고 경로만 있다. 어디서 나온 질문인지 감을 주는 용도.
        lines.append(f"근거 파일: {', '.join(question.based_on)}")
    lines.append("")
    return lines


def _truncate(text: str, max_chars: int) -> str:
    # 원질문·모범답안이 비정상적으로 길어 프롬프트를 잠식하는 것을 막는다.
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...(이하 생략)"
