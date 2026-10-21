from __future__ import annotations

import re
from collections.abc import Mapping

from app.core.common.feedback.competency.catalog import LABELS
from app.core.common.feedback.competency.mapping import CompetencyTargets
from app.core.common.feedback.competency.rubric import competency_criteria
from app.core.common.feedback.multi.prompt import system_prompt_for_job as multi_prompt
from app.core.common.feedback.rubric import AXIS_LEVEL_CRITERIA, general_axis_level_criteria
from app.core.common.feedback.solo.prompt import system_prompt_for_job as solo_prompt
from app.core.common.job_interview import specialist_job


def system_prompt(job_role: str | None, *, multi: bool = False) -> str:
    job = specialist_job(job_role)
    general = job is not None and job.family == "GENERAL"
    legacy = general_axis_level_criteria() if general else AXIS_LEVEL_CRITERIA
    prompt = multi_prompt(job_role) if multi else solo_prompt(job_role)
    prompt = prompt.replace(legacy, competency_criteria(general=general))
    prompt = re.sub(
        r"\[N:1 등급 적용 — 직책별 해석\].*?(?=\[면접관별 평가 personas\])",
        "[N:1 역량 해석]\nTECH는 직무 지식과 판단 HR은 협업·성장 CEO는 판단·실행 관점이다.\n"
        "PM과 DESIGN은 판단·협업 관점이다. 실제로 지정된 문항 역량과 근거를 우선한다.\n"
        "면접관별 점수는 서버가 계산한다. 비TECH에는 KNOWLEDGE를 두지 않는다.\n\n",
        prompt,
        flags=re.DOTALL,
    )
    prompt = prompt.replace("세 축 등급과 depth_evidence가 있고", "response와 주 역량 근거 그룹이 있고")
    prompt = prompt.replace("TECH가 아닌 면접관의 문항은 accuracy가 null인가?", "비TECH 문항에서 knowledge가 null인가?")
    return prompt


def add_target_guidance(message: str, targets: Mapping[str, CompetencyTargets]) -> str:
    # 외부 답변 본문을 정규식으로 다시 쓰지 않고 서버가 만든 매핑을 별도 블록으로 덧붙인다.
    lines = ["[서버 지정 평가 역량 — question_id별]"]
    for qid, target in targets.items():
        if target.primary:
            secondary = ", ".join(LABELS[comp] for comp in target.secondary) or "없음"
            lines.append(f"question_id: {qid} / 평가 역량: 주 = {LABELS[target.primary]} / 보조 = {secondary}")
        else:
            lines.append(
                f"question_id: {qid} / 평가 역량: 미분류 — "
                "질문과 의도로 inferred_primary 하나를 고르고 그 그룹을 채운다"
            )
    return "\n".join(lines) + "\n\n" + message
