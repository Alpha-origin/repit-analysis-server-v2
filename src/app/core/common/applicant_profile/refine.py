from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from app.core.common.applicant_profile.dto import (
    SCHEMA_VERSION,
    ApplicantProfile,
    ClaimCheck,
    Evidence,
    TroubleshootingProfile,
)
from app.core.common.applicant_profile.evidence import (
    MIN_AVAILABLE_CATEGORIES,
    available_categories,
    normalize_path,
)
from app.core.common.interview_qa.errors import PipelineError

logger = logging.getLogger(__name__)

_UNVERIFIED_TROUBLESHOOTING_NOTE = "코드에서 해결 근거를 확인하지 못했습니다."

# <저장소>/<하위 경로> 처럼 2단계 이상인 경로만 근거로 인정한다.
_MIN_PATH_DEPTH = 2


class ProfileEvidenceRefine:
    """Stage 5' — LLM 이 제출한 profile 의 근거를 실제 저장소 트리와 대조해 정리한다.

    /questions/cycle 에는 저장소 트리가 없어서 파일이 실제로 있는지 볼 수 없다.
    그 보장은 여기서만 한다. 이후 단계는 근거 경로 집합에 포함되는지만 본다.
    """

    def execute(
        self,
        raw_profile: dict[str, Any],
        path_index: dict[str, str],
        major: str | None,
    ) -> ApplicantProfile:
        try:
            draft = ApplicantProfile.model_validate(raw_profile)
        except ValidationError as exc:
            logger.warning("applicant_profile.refine.validation_failed", extra={"error": str(exc)})
            raise PipelineError(500, "종합 데이터 생성 결과가 형식을 충족하지 못했습니다.") from exc

        checker = _PathChecker(path_index)
        profile = self._refine(draft, checker).model_copy(update={"schema_version": SCHEMA_VERSION, "major": major})

        categories = available_categories(profile)
        logger.info(
            "applicant_profile.refine.done",
            extra={"available_categories": list(categories), "rejected_paths": checker.rejected},
        )
        if len(categories) < MIN_AVAILABLE_CATEGORIES:
            # 근거가 하나도 안 남은 경우도 여기에 걸린다(카테고리 0종).
            # 시스템 오류가 아니라 제출한 자료로는 질문을 만들 수 없다는 뜻이라 422 다.
            raise PipelineError(422, "코드에서 확인할 수 있는 근거가 부족합니다. 저장소나 포트폴리오를 보강해 주세요.")
        return profile

    @staticmethod
    def _refine(draft: ApplicantProfile, checker: _PathChecker) -> ApplicantProfile:
        # 근거가 0개가 된 항목은 지운다. 남겨 두면 그 항목으로 만든 질문이 basedOn 을 못 채운다.
        tech_stack = [
            item.model_copy(update={"evidence": kept})
            for item in draft.tech_stack
            if (kept := checker.filter_evidence(item.evidence))
        ]
        core_features = [
            item.model_copy(update={"evidence": kept})
            for item in draft.core_features
            if (kept := checker.filter_evidence(item.evidence))
        ]
        integrations = [
            item.model_copy(update={"evidence": kept})
            for item in draft.integrations
            if (kept := checker.filter_evidence(item.evidence))
        ]
        structure_notes = [
            note.model_copy(update={"key_paths": kept_paths})
            for note in draft.structure_notes
            if (kept_paths := checker.filter_paths(note.key_paths))
        ]

        # 트러블슈팅은 지우되 주장 자체는 claimChecks 에 unverified 로 남긴다.
        # 질문의 근거로는 못 쓰지만 "포트폴리오에 이런 주장이 있었다"는 맥락은 유효하다.
        troubleshootings: list[TroubleshootingProfile] = []
        claim_checks = list(draft.claim_checks)
        for item in draft.troubleshootings:
            kept = checker.filter_evidence(item.evidence)
            if kept:
                troubleshootings.append(item.model_copy(update={"evidence": kept}))
            else:
                claim_checks.append(_to_unverified_claim(item))

        return draft.model_copy(
            update={
                "tech_stack": tech_stack,
                "core_features": core_features,
                "troubleshootings": troubleshootings,
                "integrations": integrations,
                "claim_checks": claim_checks,
                "structure_notes": structure_notes,
            }
        )


def _to_unverified_claim(item: TroubleshootingProfile) -> ClaimCheck:
    claim = f"{item.title}: {item.portfolio_claim}" if item.portfolio_claim else item.title
    return ClaimCheck(claim=claim, status="unverified", note=_UNVERIFIED_TROUBLESHOOTING_NOTE)


class _PathChecker:
    """경로가 실제 저장소 트리에 있는지 판정한다.

    path_index 에는 파일만 들어 있다(stage3_repo_tree._walk_repo). 디렉터리 경로는
    그 경로로 시작하는 파일이 하나라도 있으면 있는 것으로 본다. 구조 질문은 디렉터리
    단위로 묻는 경우가 많아서 파일만 허용하면 근거가 어색해진다.
    """

    def __init__(self, path_index: dict[str, str]) -> None:
        self._files = frozenset(path_index)
        directories: set[str] = set()
        for path in path_index:
            parts = path.split("/")
            directories.update("/".join(parts[:depth]) for depth in range(1, len(parts)))
        self._directories = frozenset(directories)
        self.rejected = 0  # 로그용. 지운 경로 수.

    def accept(self, raw_path: str) -> str | None:
        path = normalize_path(raw_path)
        # <저장소>/<하위 경로> 처럼 2단계 이상만 인정한다. 저장소 이름만 있는 경로는
        # 저장소 안의 모든 파일과 맞아떨어져 근거로서 의미가 없다.
        segments = path.split("/")
        if len(segments) < _MIN_PATH_DEPTH or not all(segments):
            self.rejected += 1
            return None
        if path in self._files or path in self._directories:
            return path
        self.rejected += 1
        return None

    def filter_evidence(self, evidence: Sequence[Evidence]) -> list[Evidence]:
        kept: list[Evidence] = []
        seen: set[str] = set()
        for item in evidence:
            path = self.accept(item.path)
            if path is None or path in seen:
                continue
            seen.add(path)
            kept.append(item.model_copy(update={"path": path}))
        return kept

    def filter_paths(self, paths: Sequence[str]) -> list[str]:
        kept: list[str] = []
        for raw_path in paths:
            path = self.accept(raw_path)
            if path is not None and path not in kept:
                kept.append(path)
        return kept
