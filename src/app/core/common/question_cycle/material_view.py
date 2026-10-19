from __future__ import annotations

import json
from dataclasses import dataclass

from app.core.common.applicant_profile.dto_v2 import ApplicantProfileV2
from app.core.common.applicant_profile.evidence import MIN_AVAILABLE_CATEGORIES
from app.core.common.interview_qa.errors import PipelineError
from app.core.common.job_spec import JobSpec, find_job, uses_document_path


@dataclass(frozen=True)
class CycleMaterialView:
    job: JobSpec
    available_categories: tuple[str, ...]
    evidence_paths: frozenset[str]
    prompt_lines: str


def material_view(profile: ApplicantProfileV2, max_chars: int) -> CycleMaterialView:
    job = find_job(profile.job_role)
    if job is None or not uses_document_path(job.code):
        raise PipelineError(422, "비개발 profile v2의 jobRole이 올바르지 않습니다.")
    materials = [
        material for material in profile.materials if material.evidence and material.category in job.categories
    ]
    available = tuple(category for category in job.categories if any(item.category == category for item in materials))
    if len(available) < MIN_AVAILABLE_CATEGORIES or (
        job.document_evidence_required and not any(item.source_kind != "SUPPLEMENT" for item in materials)
    ):
        raise PipelineError(422, "문서·코드 근거와 2종 이상의 카테고리가 필요합니다.")
    evidence = frozenset(item.path for material in materials for item in material.evidence if item.path.strip())
    lines = [
        {
            "category": material.category,
            "title": material.title[:max_chars],
            "summary": material.summary[:max_chars],
            "sourceKind": material.source_kind,
            "evidence": [item.model_dump() for item in material.evidence],
        }
        for material in materials
    ]
    return CycleMaterialView(job, available, evidence, json.dumps(lines, ensure_ascii=False))
