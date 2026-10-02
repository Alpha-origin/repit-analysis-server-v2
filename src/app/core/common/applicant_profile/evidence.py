from __future__ import annotations

from collections.abc import Sequence

from app.core.common.applicant_profile.dto import ApplicantProfile, Evidence
from app.core.common.interview_qa.dto import QuestionCategory

# 질문 카테고리 5종. 정렬·프롬프트 나열 순서도 이 순서를 따른다.
CATEGORIES: tuple[QuestionCategory, ...] = (
    "tech_choice",
    "implementation",
    "troubleshooting",
    "integration",
    "structure",
)

# 이용 가능한 카테고리 하한. 이보다 적으면 사이클을 만들 수 없다고 보고 422 로 실패시킨다.
# MULTI 세트는 서로 다른 카테고리 2개가 있어야 성립한다. /profile 과 /questions/cycle 이
# 같은 값을 써야 해서 설정이 아니라 상수로 둔다.
MIN_AVAILABLE_CATEGORIES = 2


def normalize_path(path: str) -> str:
    # "order-api/src/" 와 "order-api/src" 가 같은 경로로 비교되게 끝 슬래시를 지운다.
    # /profile 의 근거 정리와 /questions/cycle 의 basedOn 검증이 같은 규칙을 써야 한다.
    return path.strip().rstrip("/")


def evidence_paths(profile: ApplicantProfile) -> frozenset[str]:
    """근거 경로 집합. 원질문의 basedOn 은 이 집합에서만 고른다.

    techStack·coreFeatures·troubleshootings·integrations 의 evidence[].path 에
    structureNotes[].keyPaths 를 합친 것이다. claimChecks 는 근거가 아니라서 빠진다.
    """
    paths: set[str] = set()
    for items in (profile.tech_stack, profile.core_features, profile.troubleshootings, profile.integrations):
        for item in items:
            paths.update(_paths_of(item.evidence))
    for note in profile.structure_notes:
        paths.update(normalize_path(path) for path in note.key_paths)
    paths.discard("")
    return frozenset(paths)


def available_categories(profile: ApplicantProfile) -> tuple[QuestionCategory, ...]:
    """재료 항목이 1개 이상인 카테고리. 순서는 CATEGORIES 를 따른다.

    재료는 근거가 있는 항목만 센다. /profile 결과는 근거 정리를 거쳐 늘 그렇지만,
    /questions/cycle 은 API 서버가 저장했다가 되돌려준 값을 받으므로 여기서 다시 거른다.
    """
    material = {
        "tech_choice": [item for item in profile.tech_stack if _paths_of(item.evidence)],
        "implementation": [item for item in profile.core_features if _paths_of(item.evidence)],
        "troubleshooting": [item for item in profile.troubleshootings if _paths_of(item.evidence)],
        "integration": [item for item in profile.integrations if _paths_of(item.evidence)],
        "structure": [note for note in profile.structure_notes if any(normalize_path(p) for p in note.key_paths)],
    }
    return tuple(category for category in CATEGORIES if material[category])


def _paths_of(evidence: Sequence[Evidence]) -> list[str]:
    return [path for path in (normalize_path(item.path) for item in evidence) if path]
