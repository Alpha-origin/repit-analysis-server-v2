from __future__ import annotations

import json
import logging
import tempfile
from typing import Any

from app.core.common.applicant_profile.dto import (
    ProfileCallbackSuccess,
    ProfileJobRequest,
    ProfileResult,
)
from app.core.common.applicant_profile.exploration import ProfileExploration
from app.core.common.applicant_profile.refine import ProfileEvidenceRefine
from app.core.common.applicant_profile.summary import to_project_summary
from app.core.common.interview_qa.dto import CallbackErrorDetail, CallbackFailure
from app.core.common.interview_qa.errors import PipelineError
from app.core.common.interview_qa.ports.webhook_client import WebhookClient
from app.core.common.interview_qa.stage1_validation import Stage1Validation
from app.core.common.interview_qa.stage2_document_merge import Stage2DocumentMerge
from app.core.common.interview_qa.stage2_image_llm_triage import Stage2ImageLlmTriage
from app.core.common.interview_qa.stage2_image_structuring import Stage2ImageStructuring
from app.core.common.interview_qa.stage2_image_triage import Stage2ImageTriage
from app.core.common.interview_qa.stage2_pdf_extract import Stage2PdfExtract
from app.core.common.interview_qa.stage3_repo_tree import Stage3RepoTree

logger = logging.getLogger(__name__)


class DispatchApplicantProfile:
    """/profile — 포트폴리오와 코드를 한 번 분석해 종합 데이터(profile) 를 만든다.

    Stage 1~3 은 /generate 와 같은 구현을 그대로 쓴다. /generate 는 이전이 끝나면
    삭제되므로 공통 부분을 따로 묶지 않고 단계 호출만 나란히 둔다.
    """

    def __init__(
        self,
        webhook: WebhookClient,
        stage1_validation: Stage1Validation,
        stage2_pdf_extract: Stage2PdfExtract,
        stage2_image_triage: Stage2ImageTriage,
        stage2_image_llm_triage: Stage2ImageLlmTriage,
        stage2_image_structuring: Stage2ImageStructuring,
        stage2_document_merge: Stage2DocumentMerge,
        stage3_repo_tree: Stage3RepoTree,
        profile_exploration: ProfileExploration,
        profile_refine: ProfileEvidenceRefine,
    ) -> None:
        # 콜백 전송 어댑터. 구현체는 DI 가 결정.
        self._webhook = webhook
        # 1단계 — PDF·GitHub 입력 검증.
        self._stage1 = stage1_validation
        # 2단계 — PDF 추출·이미지 트리아지·구조화·병합.
        self._stage2_pdf = stage2_pdf_extract
        self._stage2_triage = stage2_image_triage
        self._stage2_llm_triage = stage2_image_llm_triage
        self._stage2_structuring = stage2_image_structuring
        self._stage2_merge = stage2_document_merge
        # 3단계 — GitHub tarball 취득 + 역할 판정 + 트리.
        self._stage3 = stage3_repo_tree
        # 4'단계 — LLM 코드 탐색 세션. submit_profile 로 끝난다.
        self._exploration = profile_exploration
        # 5'단계 — 근거를 실제 트리와 대조해 정리.
        self._refine = profile_refine

    async def execute(self, job_id: str, job_request: ProfileJobRequest) -> None:
        logger.info(
            "applicant_profile.dispatch.start",
            extra={"job_id": job_id, "github_repo_count": len(job_request.github_urls)},
        )

        try:
            payload = await self._build_payload(job_id, job_request)
        except Exception:
            # 알 수 없는 내부 오류 — 500 으로 콜백 전송.
            # 백그라운드 작업은 예외를 응답으로 알릴 수 없어서, 여기서 반드시 삼켜야 한다.
            logger.exception("applicant_profile.dispatch.unexpected_error", extra={"job_id": job_id})
            payload = _failure_payload(job_id, 500, "내부 오류로 작업을 완료하지 못했습니다.")

        logger.debug("applicant_profile.dispatch.payload payload=%s", json.dumps(payload, ensure_ascii=False))
        await self._webhook.send(job_request.callback_url, payload)
        logger.info("applicant_profile.dispatch.done", extra={"job_id": job_id})

    async def _build_payload(self, job_id: str, job_request: ProfileJobRequest) -> dict[str, Any]:
        try:
            validated = await self._stage1.execute(
                portfolio_url=job_request.portfolio_url,
                github_urls=job_request.github_urls,
            )

            parsed_portfolio = await self._stage2_pdf.execute(validated.pdf_bytes)
            triaged = await self._stage2_triage.execute(parsed_portfolio)
            llm_triaged = await self._stage2_llm_triage.execute(triaged)
            structured = await self._stage2_structuring.execute(llm_triaged)
            merged = await self._stage2_merge.execute(structured)

            # tarball 풀어 놓은 디렉터리가 탐색 세션의 read_files 호출까지 살아 있어야 한다.
            # 근거 정리는 path_index 만 쓰므로 디렉터리 밖에서 해도 된다.
            with tempfile.TemporaryDirectory(prefix="applicant_profile_") as tmpdir:
                repos_tree = await self._stage3.execute(repos=validated.repos, working_dir=tmpdir)
                raw_profile = await self._exploration.execute(
                    portfolio_text=merged.portfolio_text,
                    repos_tree=repos_tree,
                    major=job_request.major,
                )

            profile = self._refine.execute(raw_profile, repos_tree.path_index, job_request.major)
        except PipelineError as exc:
            return _failure_payload(job_id, exc.status_code, exc.message)

        return ProfileCallbackSuccess(
            job_id=job_id,
            result=ProfileResult(profile=profile, project_summary=to_project_summary(profile)),
        ).model_dump(by_alias=True)


def _failure_payload(job_id: str, status_code: int, message: str) -> dict[str, Any]:
    return CallbackFailure(
        job_id=job_id,
        error=CallbackErrorDetail(status_code=status_code, message=message),
    ).model_dump(by_alias=True)
