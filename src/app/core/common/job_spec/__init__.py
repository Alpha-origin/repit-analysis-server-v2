from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.core.common.applicant_profile.evidence import CATEGORIES
from app.core.common.job_spec.prompt_data import CATEGORY_DETAILS, CATEGORY_HINTS


@dataclass(frozen=True)
class DocTypeSpec:
    code: str
    label: str
    reading_points: str
    row_cap: int | None = None


@dataclass(frozen=True)
class JobSpec:
    code: str
    label: str
    family: Literal["DEV", "NON_DEV", "GENERAL"]
    github: Literal["REQUIRED", "OPTIONAL", "HIDDEN"]
    required_docs: frozenset[str]
    allowed_docs: frozenset[str]
    supplements: bool
    categories: tuple[str, ...]
    category_labels: tuple[str, ...]
    category_descriptions: tuple[str, ...] = ()
    category_hints: tuple[tuple[str, tuple[str, ...]], ...] = ()
    cover_letters: bool = False
    material_rule: Literal["REQUIRED_TYPE", "NON_RESUME_OR_COVER_LETTER"] = "REQUIRED_TYPE"
    document_evidence_required: bool = False


DOC_TYPES = {
    code: DocTypeSpec(code, label, points, cap)
    for code, label, points, cap in (
        ("WORK_SAMPLE", "업무 산출물", "업무 목표, 본인 역할, 구성, 판단 근거, 결과·수치", None),
        ("RESUME", "이력서", "경력·프로젝트 목록, 기간, 역할. 다른 문서의 프로젝트를 찾는 색인", None),
        ("CAREER_DESCRIPTION", "경력기술서", "프로젝트별 본인 역할, 한 일, 결과", None),
        ("PORTFOLIO", "포트폴리오", "프로젝트별 문제, 본인 역할, 판단, 결과", None),
        ("ANALYSIS_REPORT", "분석 리포트", "문제 정의, 지표, 데이터, 전처리, 방법, 결과, 한계, 의사결정", None),
        ("COMPETITION_SUMMARY", "대회 결과 요약", "과제, 평가 지표, 접근, 개선 시도, 순위", None),
        ("INCIDENT_REPORT", "장애 대응 보고서", "증상, 영향, 탐지, 원인, 조치, 재발 방지, 타임라인", None),
        ("TABLE_SPEC", "테이블 정의서", "테이블·키·인덱스·제약, 테이블 수와 관계", None),
        ("SYSTEM_ARCHITECTURE", "시스템 구성도", "구성요소, 연결, 이중화, 네트워크 경계", None),
        ("TEST_PLAN", "테스트 계획서", "범위, 위험, 전략, 환경, 진입·종료 기준", None),
        ("TEST_CASE", "테스트 케이스", "케이스 ID, 전제, 절차, 기대 결과, 우선순위, 경계값·예외", 80),
        ("BUG_REPORT", "버그 리포트", "재현 절차, 기대·실제 결과, 심각도, 원인, 처리", 40),
        ("VULNERABILITY_REPORT", "취약점 분석 보고서", "대상, 취약점, 재현, 영향, 위험도, 대응·권고", None),
        ("CTF_WRITEUP", "CTF 풀이", "문제 유형, 접근, 실패한 시도, 풀이 핵심, 도구", None),
        ("PRD", "기획서(PRD)", "배경·문제, 목표 지표, 사용자, 요구사항, 우선순위, 범위 제외", None),
        ("POLICY_DOC", "정책서", "규칙, 예외, 상태 전이, 권한", None),
        ("REQUIREMENT_SPEC", "요구사항 정의서", "요구사항 ID, 내용, 우선순위, 출처", 80),
        ("SCREEN_SPEC", "화면설계서·플로우", "화면 흐름, 화면별 기능 설명", None),
        ("PROPOSAL", "제안서", "고객 과제, 현황, 해결안, 대안, 비용·효과, 일정", None),
        ("PROJECT_REPORT", "수행·결과 보고서", "수행 범위, 결과, 이슈와 대응, 성과 지표", None),
    )
}


def _nondev(
    code: str,
    label: str,
    required: str,
    optional: str,
    categories: str,
    *,
    hidden: bool = False,
) -> JobSpec:
    required_set = frozenset(required.split())
    return JobSpec(
        code,
        label,
        "NON_DEV",
        "HIDDEN" if hidden else "OPTIONAL",
        required_set,
        required_set | frozenset(optional.split()) | {"RESUME", "CAREER_DESCRIPTION"},
        hidden,
        tuple(categories.split()),
        tuple(label for _, label, _ in CATEGORY_DETAILS[code]),
        tuple(description for _, _, description in CATEGORY_DETAILS[code]),
        CATEGORY_HINTS[code],
        document_evidence_required=True,
    )


JOBS: dict[str, JobSpec] = {
    code: JobSpec(
        code,
        label,
        "DEV",
        "REQUIRED",
        frozenset({"PORTFOLIO"}),
        frozenset({"PORTFOLIO", "RESUME", "CAREER_DESCRIPTION"}),
        False,
        tuple(CATEGORIES),
        (),
    )
    for code, label in (("FRONTEND", "프론트엔드"), ("BACKEND", "백엔드"), ("WEB_PUBLISHER", "웹 퍼블리셔"))
}
for _spec in (
    _nondev(
        "DATA_ANALYST",
        "데이터 분석가",
        "PORTFOLIO ANALYSIS_REPORT",
        "COMPETITION_SUMMARY",
        "hypothesis data_prep method interpretation impact",
    ),
    _nondev(
        "DBA_INFRA",
        "DBA·인프라 엔지니어",
        "PORTFOLIO CAREER_DESCRIPTION",
        "INCIDENT_REPORT TABLE_SPEC SYSTEM_ARCHITECTURE",
        "design performance operations incident automation",
    ),
    _nondev(
        "QA_ENGINEER",
        "QA 엔지니어",
        "TEST_PLAN TEST_CASE",
        "PORTFOLIO BUG_REPORT",
        "test_design defect_reporting release_judgment automation quality_process",
    ),
    _nondev(
        "SECURITY",
        "정보보안",
        "VULNERABILITY_REPORT CTF_WRITEUP",
        "PORTFOLIO",
        "vuln_principle analysis_process mitigation incident_response ethics_law",
    ),
    _nondev(
        "PRODUCT_MANAGER",
        "서비스 기획자 (PM·PO)",
        "PRD POLICY_DOC REQUIREMENT_SPEC",
        "PORTFOLIO SCREEN_SPEC",
        "problem_definition prioritization metrics collaboration retrospective",
        hidden=True,
    ),
    _nondev(
        "IT_CONSULTANT",
        "IT 컨설턴트·기술영업",
        "CAREER_DESCRIPTION PORTFOLIO",
        "PROPOSAL PROJECT_REPORT REQUIREMENT_SPEC",
        "requirement_analysis solution_design client_communication delivery outcome",
        hidden=True,
    ),
):
    JOBS[_spec.code] = _spec


def find_job(code: str | None) -> JobSpec | None:
    return JOBS.get(code.strip().upper()) if code else None


def is_non_dev(code: str | None) -> bool:
    job = find_job(code)
    return job is not None and job.family == "NON_DEV"


GENERAL_DOCS = frozenset({"RESUME", "CAREER_DESCRIPTION", "PORTFOLIO", "WORK_SAMPLE"})


def _general(code: str, label: str) -> JobSpec:
    return JobSpec(
        code,
        label,
        "GENERAL",
        "HIDDEN",
        frozenset(),
        GENERAL_DOCS,
        False,
        tuple(category for category, _, _ in CATEGORY_DETAILS[code]),
        tuple(label for _, label, _ in CATEGORY_DETAILS[code]),
        tuple(description for _, _, description in CATEGORY_DETAILS[code]),
        cover_letters=True,
        material_rule="NON_RESUME_OR_COVER_LETTER",
    )


for _code, _label in (
    ("GEN_STRATEGY", "경영기획·전략"),
    ("GEN_HR_ADMIN", "인사·총무"),
    ("GEN_FINANCE", "재무·회계"),
    ("GEN_MARKETING", "마케팅·홍보"),
    ("GEN_SALES", "영업·고객관리"),
    ("GEN_DESIGN", "디자인"),
):
    JOBS[_code] = _general(_code, _label)


def uses_document_path(code: str | None) -> bool:
    job = find_job(code)
    return job is not None and job.family in ("NON_DEV", "GENERAL")
