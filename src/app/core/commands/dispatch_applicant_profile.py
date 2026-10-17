from __future__ import annotations

import json
import logging
import tempfile
from typing import Any

import fitz

from app.core.common.applicant_profile.build_v2 import DocumentProfileBuild, UnreadableDocumentError
from app.core.common.applicant_profile.dto import (
    ProfileCallbackErrorDetail,
    ProfileCallbackFailure,
    ProfileCallbackSuccess,
    ProfileFailureCode,
    ProfileJobRequest,
    ProfileResult,
)
from app.core.common.applicant_profile.exploration import ProfileExploration
from app.core.common.applicant_profile.refine import InsufficientEvidenceError, ProfileEvidenceRefine
from app.core.common.applicant_profile.summary import to_project_summary
from app.core.common.interview_qa.errors import PipelineError
from app.core.common.interview_qa.ports.webhook_client import WebhookClient
from app.core.common.interview_qa.stage1_validation import Stage1Validation
from app.core.common.interview_qa.stage2_document_merge import Stage2DocumentMerge
from app.core.common.interview_qa.stage2_image_llm_triage import Stage2ImageLlmTriage
from app.core.common.interview_qa.stage2_image_structuring import Stage2ImageStructuring
from app.core.common.interview_qa.stage2_image_triage import Stage2ImageTriage
from app.core.common.interview_qa.stage2_nondev.dto import ProfileDocument
from app.core.common.interview_qa.stage2_nondev.extract import MAX_PAGES
from app.core.common.interview_qa.stage2_pdf_extract import Stage2PdfExtract
from app.core.common.interview_qa.stage3_repo_tree import Stage3RepoTree
from app.core.common.job_spec import DOC_TYPES, find_job

logger = logging.getLogger(__name__)

# 근거 부족(InsufficientEvidenceError) 밖의 4xx 는 Stage 1 의 입력 검증에서만 나온다.
_FAILURE_CODE_BY_STATUS: dict[int, ProfileFailureCode] = {403: "REPO_NOT_PUBLIC", 422: "INVALID_MATERIAL"}


class DispatchApplicantProfile:
    """/profile — 포트폴리오와 코드를 한 번 분석해 종합 데이터(profile) 를 만든다.

    Stage 1~3(입력 검증 → PDF 처리 → 저장소 트리) 을 거친 뒤 코드 탐색으로 재료를 모은다.
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
        document_profile: DocumentProfileBuild | None = None,
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
        self._document_profile = document_profile

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
            payload = _failure_payload(job_id, 500, "내부 오류로 작업을 완료하지 못했습니다.", "INTERNAL")

        logger.debug("applicant_profile.dispatch.payload payload=%s", json.dumps(payload, ensure_ascii=False))
        await self._webhook.send(job_request.callback_url, payload)
        logger.info("applicant_profile.dispatch.done", extra={"job_id": job_id})

    async def _build_payload(self, job_id: str, job_request: ProfileJobRequest) -> dict[str, Any]:
        try:
            job = find_job(job_request.job_role)
            if job is not None and job.family == "NON_DEV":
                if self._document_profile is None:
                    raise PipelineError(500, "문서 분석 처리기가 연결되지 않았습니다.")
                nondev = await self._document_profile.execute(
                    job,
                    job_request.documents,
                    job_request.supplements,
                    job_request.github_urls,
                )
                return ProfileCallbackSuccess(
                    job_id=job_id,
                    result=ProfileResult(
                        profile=nondev,
                        project_summary=to_project_summary(nondev),
                    ),
                ).model_dump(by_alias=True)
            documents = job_request.documents if job_request.job_role else ()
            portfolio_url = documents[0].url if documents else job_request.portfolio_url
            if portfolio_url is None:
                raise PipelineError(422, "포트폴리오 PDF가 필요합니다.")
            validated = await self._stage1.execute(
                portfolio_url=portfolio_url,
                github_urls=job_request.github_urls,
            )
            dev_pdfs = []
            total_pages = 0
            for index, document in enumerate(documents):
                pdf = validated.pdf_bytes if index == 0 else await self._stage1._validate_pdf(document.url)
                total_pages += _check_document_pdf(pdf, f"doc-{index + 1}")
                if total_pages > MAX_PAGES:
                    raise PipelineError(422, "PDF 합계는 50쪽까지 분석할 수 있습니다.")
                dev_pdfs.append(pdf)

            parsed_portfolio = await self._stage2_pdf.execute(validated.pdf_bytes)
            triaged = await self._stage2_triage.execute(parsed_portfolio)
            llm_triaged = await self._stage2_llm_triage.execute(triaged)
            structured = await self._stage2_structuring.execute(llm_triaged)
            merged = await self._stage2_merge.execute(structured)
            portfolio_text = merged.portfolio_text
            if len(documents) > 1:
                used_images = (
                    min(self._stage2_structuring._max_images, sum(len(page.image_blocks) for page in llm_triaged.pages))
                    if llm_triaged.branch == "image_heavy"
                    else 0
                )
                portfolio_text = await self._merge_dev_documents(
                    documents,
                    tuple(dev_pdfs),
                    portfolio_text,
                    self._stage2_structuring._max_images - used_images,
                )

            # tarball 풀어 놓은 디렉터리가 탐색 세션의 read_files 호출까지 살아 있어야 한다.
            # 근거 정리는 path_index 만 쓰므로 디렉터리 밖에서 해도 된다.
            with tempfile.TemporaryDirectory(prefix="applicant_profile_") as tmpdir:
                repos_tree = await self._stage3.execute(repos=validated.repos, working_dir=tmpdir)
                raw_profile = await self._exploration.execute(
                    portfolio_text=portfolio_text,
                    repos_tree=repos_tree,
                    major=job_request.job_role or job_request.major,
                )

            profile = self._refine.execute(
                raw_profile, repos_tree.path_index, job_request.job_role or job_request.major
            )
        except PipelineError as exc:
            return _failure_payload(job_id, exc.status_code, exc.message, _failure_code(exc))

        return ProfileCallbackSuccess(
            job_id=job_id,
            result=ProfileResult(profile=profile, project_summary=to_project_summary(profile)),
        ).model_dump(by_alias=True)

    async def _merge_dev_documents(
        self,
        documents: tuple[ProfileDocument, ...],
        pdfs: tuple[bytes, ...],
        first_text: str,
        remaining_images: int,
    ) -> str:
        # Multiple developer documents retain the existing extraction path; no page-reader calls.
        texts = []
        for index, document in enumerate(documents):
            pdf = pdfs[index]
            if index == 0:
                text = first_text
            else:
                parsed = await self._stage2_pdf.execute(pdf)
                triaged = await self._stage2_triage.execute(parsed)
                llm_triaged = await self._stage2_llm_triage.execute(triaged)
                structured = await self._stage2_structuring.execute(llm_triaged, image_budget=remaining_images)
                if llm_triaged.branch == "image_heavy":
                    remaining_images -= min(remaining_images, sum(len(page.image_blocks) for page in llm_triaged.pages))
                text = (await self._stage2_merge.execute(structured)).portfolio_text
            texts.append(f"[doc-{index + 1} · {DOC_TYPES[document.type].label} · {document.title or ''}]\n{text}")
        return "\n".join(texts)


def _check_document_pdf(data: bytes, document_id: str) -> int:
    try:
        with fitz.open(stream=data, filetype="pdf") as pdf:
            if pdf.needs_pass or not 1 <= pdf.page_count <= MAX_PAGES:
                raise ValueError("PDF is encrypted, empty or too long")
            return int(pdf.page_count)
    except Exception as exc:
        raise PipelineError(422, f"{document_id}: 암호화·손상 PDF이거나 50쪽을 넘었습니다.") from exc


def _failure_code(exc: PipelineError) -> ProfileFailureCode:
    if isinstance(exc, UnreadableDocumentError):
        return "UNREADABLE_DOCUMENT"
    if isinstance(exc, InsufficientEvidenceError):
        return "INSUFFICIENT_EVIDENCE"
    return _FAILURE_CODE_BY_STATUS.get(exc.status_code, "INTERNAL")


def _failure_payload(job_id: str, status_code: int, message: str, code: ProfileFailureCode) -> dict[str, Any]:
    return ProfileCallbackFailure(
        job_id=job_id,
        error=ProfileCallbackErrorDetail(status_code=status_code, message=message, code=code),
    ).model_dump(by_alias=True)
