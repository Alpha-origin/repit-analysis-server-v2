from __future__ import annotations

from collections import defaultdict

from app.core.common.applicant_profile.dto_v2 import (
    ApplicantProfileV2,
    DraftProfileV2,
    EvidenceV2,
    MaterialOut,
    SourceKind,
    SourceV2,
)
from app.core.common.applicant_profile.refine import InsufficientEvidenceError, _PathChecker
from app.core.common.interview_qa.stage2_nondev.dto import EvidenceBlock
from app.core.common.interview_qa.stage2_nondev.masking import mask_text, normalize_quote
from app.core.common.job_spec import DOC_TYPES, JobSpec

MIN_QUOTE = 8
MAX_QUOTE = 80
PRIORITY: tuple[SourceKind, ...] = ("DOCUMENT", "VISION", "CODE", "SUPPLEMENT")


class ProfileEvidenceRefineV2:
    def execute(
        self,
        draft: DraftProfileV2,
        job: JobSpec,
        blocks: list[EvidenceBlock],
        sources: list[SourceV2],
        path_index: dict[str, str],
    ) -> ApplicantProfileV2:
        index: dict[str, list[EvidenceBlock]] = defaultdict(list)
        for block in blocks:
            if block.method != "VISION_RELATION":
                index[block.path].append(block)
        checker = _PathChecker(path_index)
        materials = []
        for material in draft.materials:
            if material.category not in job.categories:
                continue
            evidence, kinds = self._filter(material.evidence, index, checker)
            if evidence:
                materials.append(
                    MaterialOut(
                        category=material.category,
                        title=mask_text(material.title),
                        summary=mask_text(material.summary),
                        evidence=evidence,
                        source_kind=next(kind for kind in PRIORITY if kind in kinds),
                    )
                )
        categories = {material.category for material in materials}
        backed = {material.category for material in materials if material.source_kind != "SUPPLEMENT"}
        if len(categories) < 2 or not backed:  # noqa: PLR2004
            missing = [
                label
                for category, label in zip(job.categories, job.category_labels, strict=True)
                if category not in categories
            ]
            documents = ", ".join(DOC_TYPES[code].label for code in sorted(job.allowed_docs))
            raise InsufficientEvidenceError(
                f"{job.label}: 근거 있는 카테고리 2개와 문서·코드 근거 1개가 필요합니다. "
                f"부족한 영역: {', '.join(missing)}. 자료 종류: {documents}",
            )
        claim_checks = [
            claim.model_copy(
                update={
                    "claim": mask_text(claim.claim),
                    "reason": mask_text(claim.reason),
                    "evidence": self._filter(claim.evidence, index, checker)[0],
                }
            )
            for claim in draft.claim_checks
        ]
        repos = {path.split("/", 1)[0] for path in path_index}
        return ApplicantProfileV2(
            job_role=job.code,
            overview=mask_text(draft.overview),
            sources=sources,
            materials=materials,
            claim_checks=claim_checks,
            repositories=[repo for repo in draft.repositories if repo.repo in repos],
        )

    @staticmethod
    def _filter(
        candidates: list[EvidenceV2],
        index: dict[str, list[EvidenceBlock]],
        checker: _PathChecker,
    ) -> tuple[list[EvidenceV2], set[SourceKind]]:
        kept, kinds, seen = [], set(), set()
        for evidence in candidates:
            path = evidence.path.strip()
            quote = mask_text(evidence.quote)
            normalized = normalize_quote(quote)
            if (path, normalized) in seen:
                continue
            matched: set[SourceKind] = set()
            if path.startswith(("doc-", "supp-")):
                if len(normalized) < MIN_QUOTE or len(evidence.quote) > MAX_QUOTE:
                    continue
                for block in index.get(path, []):
                    if normalized in normalize_quote(block.text):
                        kind: SourceKind = (
                            "SUPPLEMENT"
                            if path.startswith("supp-")
                            else ("VISION" if block.method == "VISION_TRANSCRIPT" else "DOCUMENT")
                        )
                        matched.add(kind)
            elif (accepted := checker.accept(path)) is not None:
                path = accepted
                matched.add("CODE")
            if matched:
                kept.append(EvidenceV2(path=path, quote=quote, note=mask_text(evidence.note)))
                kinds.update(matched)
                seen.add((path, normalized))
        return kept, kinds
