"""면접 Q&A 생성 작업 디스패처 (use case).

라우터가 ``BackgroundTasks`` 로 ``execute(job_id, job_request)`` 를 호출하면:
1. 파이프라인 1~5단계를 실행해 ``InterviewQaResult`` 를 만들고
2. 결과(또는 오류)를 ``callback_url`` 로 POST 한다.

현재는 Stage 1(입력 검증) + Stage 2-1(PDF 추출 + 노이즈 제거) 까지 구현되어 있고,
나머지 단계는 미구현 stub. 두 단계를 통과하면 501 페이로드를 콜백으로 보낸다.
"""

from __future__ import annotations

import json
import logging
import tempfile
from typing import Any

from pydantic import ValidationError

from app.core.common.interview_qa.dto import (
    CallbackErrorDetail,
    CallbackFailure,
    CallbackSuccess,
    InterviewQaResult,
    JobRequest,
)
from app.core.common.interview_qa.errors import PipelineError
from app.core.common.interview_qa.ports.webhook_client import WebhookClient
from app.core.common.interview_qa.stage1_validation import Stage1Validation
from app.core.common.interview_qa.stage2_document_merge import Stage2DocumentMerge
from app.core.common.interview_qa.stage2_image_llm_triage import Stage2ImageLlmTriage
from app.core.common.interview_qa.stage2_image_structuring import Stage2ImageStructuring
from app.core.common.interview_qa.stage2_image_triage import Stage2ImageTriage
from app.core.common.interview_qa.stage2_pdf_extract import Stage2PdfExtract
from app.core.common.interview_qa.stage3_repo_tree import Stage3RepoTree
from app.core.common.interview_qa.stage4_llm_session import Stage4LlmSession

logger = logging.getLogger(__name__)


class DispatchInterviewQa:
    """백그라운드 작업 진입점. 파이프라인 실행 + 콜백 전송을 책임진다."""

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
        stage4_llm_session: Stage4LlmSession,
    ) -> None:
        # 콜백 전송 어댑터. 구현체는 DI 가 결정.
        self._webhook = webhook
        # 1단계 — PDF·GitHub 입력 검증.
        self._stage1 = stage1_validation
        # 2-1단계 — PDF 텍스트·이미지 추출 + 노이즈 제거.
        self._stage2_pdf = stage2_pdf_extract
        # 2-2단계 — 규칙 기반 1차 이미지 트리아지 + 분기 판정.
        self._stage2_triage = stage2_image_triage
        # 2-3단계 — image_heavy 분기 전용 LLM 2차 트리아지.
        self._stage2_llm_triage = stage2_image_llm_triage
        # 2-4단계 — image_heavy 분기 전용 비전 구조화.
        self._stage2_structuring = stage2_image_structuring
        # 2-5단계 — 텍스트/이미지 합쳐 단일 통합 문서로.
        self._stage2_merge = stage2_document_merge
        # 3단계 — GitHub tarball 취득 + 역할 판정 + 트리.
        self._stage3 = stage3_repo_tree
        # 4단계 — LLM 코드 탐색 세션 + 최종 JSON 생성.
        self._stage4 = stage4_llm_session

    async def execute(self, job_id: str, job_request: JobRequest) -> None:
        """주어진 ``job_id`` 에 대해 작업을 실행하고 콜백을 전송한다.

        이 메서드는 어떤 예외도 외부로 전파하지 않는다(백그라운드에서 호출되므로
        예외가 전파되면 추적이 어렵다). 모든 오류는 콜백 페이로드로 변환한다.
        """
        logger.info(
            "dispatch.start",
            extra={"job_id": job_id, "github_repo_count": len(job_request.github_urls)},
        )

        try:
            payload = await self._build_payload(job_id, job_request)
            logger.info("dispatch.payload payload=%s", json.dumps(payload, ensure_ascii=False))
        except Exception:
            # 알 수 없는 내부 오류 — 500 으로 콜백 전송.
            logger.exception("dispatch.unexpected_error", extra={"job_id": job_id})
            payload = CallbackFailure(
                job_id=job_id,
                error=CallbackErrorDetail(
                    status_code=500,
                    message="내부 오류로 작업을 완료하지 못했습니다.",
                ),
            ).model_dump()

        await self._webhook.send(job_request.callback_url, payload)
        logger.info("dispatch.done", extra={"job_id": job_id})

    async def _build_payload(self, job_id: str, job_request: JobRequest) -> dict[str, Any]:
        """파이프라인을 돌려 콜백 페이로드(dict) 를 만든다.

        흐름:
        - Stage 1 검증 → 2-x → Stage 3(저장소 트리) → 다음 단계 stub.
        - 임시 디렉터리는 ``TemporaryDirectory`` 컨텍스트로 묶어 4단계 read_files 까지 살린다.
        - PipelineError 발생 → 그대로 실패 페이로드로 변환.
        """
        try:
            # ----- Stage 1: 입력 검증 -----
            validated = await self._stage1.execute(
                portfolio_url=job_request.portfolio_url,
                github_urls=job_request.github_urls,
            )

            # ----- Stage 2-1: PDF 추출 + 노이즈 제거 -----
            parsed_portfolio = await self._stage2_pdf.execute(validated.pdf_bytes)

            # ----- Stage 2-2: 1차 이미지 트리아지 + 분기 판정 -----
            triaged = await self._stage2_triage.execute(parsed_portfolio)

            # ----- Stage 2-3: image_heavy 분기 전용 LLM 2차 트리아지 -----
            llm_triaged = await self._stage2_llm_triage.execute(triaged)

            # ----- Stage 2-4: image_heavy 분기 전용 비전 구조화 -----
            structured = await self._stage2_structuring.execute(llm_triaged)

            # ----- Stage 2-5: 통합 문서 병합(텍스트 + 이미지 구조화 결과) -----
            merged = await self._stage2_merge.execute(structured)

            # ----- Stage 3 / 4: 저장소 트리 + LLM 탐색 세션 -----
            # tarball 풀어 놓은 디렉터리가 4단계 read_files 호출까지 살아 있어야 한다.
            with tempfile.TemporaryDirectory(prefix="interview_qa_") as tmpdir:
                repos_tree = await self._stage3.execute(
                    repos=validated.repos,
                    working_dir=tmpdir,
                )
                raw_result = await self._stage4.execute(
                    portfolio_text=merged.portfolio_text,
                    repos_tree=repos_tree,
                )

            # ----- Stage 5: 최종 JSON 검증 + 성공 페이로드 -----
            return self._build_success_payload(job_id, raw_result)
        except PipelineError as exc:
            return CallbackFailure(
                job_id=job_id,
                error=CallbackErrorDetail(status_code=exc.status_code, message=exc.message),
            ).model_dump()

    @staticmethod
    def _build_success_payload(job_id: str, raw_result: dict[str, Any]) -> dict[str, Any]:
        """4단계가 돌려준 원시 dict 를 ``InterviewQaResult`` 로 검증해 성공 페이로드로 만든다.

        LLM tool 응답에 약간의 누락이 있을 수 있어 Pydantic 으로 한번 더 검증한다.
        검증 실패 시 ``PipelineError(500)`` — 외부에서 일반 실패 페이로드로 잡힌다.
        """
        try:
            validated = InterviewQaResult.model_validate(raw_result)
        except ValidationError as exc:
            logger.warning("dispatch.result_validation_failed", extra={"error": str(exc)})
            raise PipelineError(500, "면접 질문 생성 결과가 형식을 충족하지 못했습니다.") from exc
        return CallbackSuccess(job_id=job_id, result=validated).model_dump()
