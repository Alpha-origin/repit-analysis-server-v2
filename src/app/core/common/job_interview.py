from __future__ import annotations

from app.core.common.job_spec import JobSpec, find_job

_INTERVIEWER_TITLES = {
    "DATA_ANALYST": "데이터 분석 전문 면접관",
    "DBA_INFRA": "DBA·인프라 전문 면접관",
    "QA_ENGINEER": "QA 전문 면접관",
    "SECURITY": "정보보안 전문 면접관",
    "PRODUCT_MANAGER": "서비스 기획 전문 면접관",
    "IT_CONSULTANT": "IT 컨설팅 전문 면접관",
}


def specialist_job(job_role: str | None) -> JobSpec | None:
    job = find_job(job_role)
    return job if job is not None and job.family != "DEV" else None


def interviewer_title(job: JobSpec) -> str:
    return _INTERVIEWER_TITLES.get(job.code, f"{job.label} 전문 면접관")
