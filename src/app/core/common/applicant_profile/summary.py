from __future__ import annotations

from app.core.common.applicant_profile.dto import ApplicantProfile
from app.core.common.interview_qa.dto import CoreFeature, ProjectSummary, RepositorySummary


def to_project_summary(profile: ApplicantProfile) -> ProjectSummary:
    """tailor/multi 입력용 요약. LLM 을 다시 부르지 않고 profile 에서 그대로 옮긴다.

    근거 정리에서 근거 없는 기능이 빠지므로 /generate 시절보다 기능 목록이 짧을 수 있다.
    의도된 결과다 — 확인되지 않은 기능으로 비개발 질문을 만들지 않게 한다.
    """
    return ProjectSummary(
        overview=profile.overview,
        tech_stack=[item.name for item in profile.tech_stack],
        # 저장소별 techStack 은 ProjectSummary 에 자리가 없어 버린다.
        repositories=[
            RepositorySummary(repo=repository.repo, role=repository.role, description=repository.description)
            for repository in profile.repositories
        ],
        # implementation 과 근거의 note 는 버리고 경로만 중복 없이 옮긴다.
        core_features=[
            CoreFeature(
                name=feature.name,
                description=feature.description,
                based_on=list(dict.fromkeys(evidence.path for evidence in feature.evidence)),
            )
            for feature in profile.core_features
        ],
    )
