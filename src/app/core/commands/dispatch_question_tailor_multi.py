from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.core.common.interview_qa.errors import PipelineError
from app.core.common.interview_qa.ports.webhook_client import WebhookClient
from app.core.common.question_tailor.dto import CandidateProfile, TailoredQuestion
from app.core.common.question_tailor.multi.dto import (
    NEW_QUESTION_ID_START,
    GeneratedQuestion,
    MultiTailorCallbackFailure,
    MultiTailorCallbackSuccess,
    MultiTailoredQuestion,
    MultiTailorErrorDetail,
    MultiTailorRequest,
    MultiTailorResult,
)
from app.core.common.question_tailor.multi.generate import MultiQuestionGenerate
from app.core.common.question_tailor.rewrite import QuestionRewrite

logger = logging.getLogger(__name__)


class DispatchQuestionTailorMulti:
    def __init__(
        self,
        webhook: WebhookClient,
        question_generate: MultiQuestionGenerate,
        question_rewrite: QuestionRewrite,
    ) -> None:
        # 콜백 전송 어댑터. 구현체는 DI 가 결정.
        self._webhook = webhook
        # 1단계 — 비개발 면접관 질문 생성. 실패하면 PipelineError.
        self._generate = question_generate
        # 2단계 — 기술 원질문 리텍스팅. solo 와 같은 구현을 그대로 쓴다.
        self._rewrite = question_rewrite

    async def execute(self, job_id: str, job_request: MultiTailorRequest) -> None:
        logger.info(
            "question_tailor_multi.dispatch.start",
            extra={
                "job_id": job_id,
                "interview_id": job_request.interview_id,
                "persona_count": len(job_request.other_personas) + 1,
            },
        )

        try:
            payload = await self._build_payload(job_id, job_request)
        except Exception:
            # 알 수 없는 내부 오류 — 500 으로 콜백 전송.
            # 백그라운드 작업은 예외를 응답으로 알릴 수 없어서, 여기서 반드시 삼켜야 한다.
            logger.exception("question_tailor_multi.dispatch.unexpected_error", extra={"job_id": job_id})
            payload = _failure_payload(
                job_id,
                job_request.interview_id,
                500,
                "내부 오류로 작업을 완료하지 못했습니다.",
            )

        logger.debug("question_tailor_multi.dispatch.payload payload=%s", json.dumps(payload, ensure_ascii=False))
        await self._webhook.send(job_request.callback_url, payload)
        logger.info("question_tailor_multi.dispatch.done", extra={"job_id": job_id})

    async def _build_payload(self, job_id: str, job_request: MultiTailorRequest) -> dict[str, Any]:
        try:
            generated, rewritten = await self._run_stages(job_request)
        except PipelineError as exc:
            return _failure_payload(job_id, job_request.interview_id, exc.status_code, exc.message)

        questions = self._assemble(job_request, generated, rewritten)
        logger.info(
            "question_tailor_multi.dispatch.tailored",
            extra={"job_id": job_id, "question_count": len(questions)},
        )
        return MultiTailorCallbackSuccess(
            job_id=job_id,
            interview_id=job_request.interview_id,
            result=MultiTailorResult(questions=questions),
        ).model_dump(by_alias=True)

    async def _run_stages(
        self,
        job_request: MultiTailorRequest,
    ) -> tuple[tuple[GeneratedQuestion, ...], tuple[TailoredQuestion, ...]]:
        # 생성과 리텍스팅은 서로를 참조하지 않으므로 병렬로 돌린다.
        # return_exceptions=True 로 받는 이유는, 한쪽이 먼저 실패해도 다른 쪽을
        # 끝까지 기다렸다가 정리하기 위해서다(gather 는 나머지를 취소해 주지 않는다).
        generate_task = self._generate.execute(
            personas=job_request.other_personas,
            project_summary=job_request.project_summary,
            tech_questions=job_request.questions,
        )
        rewrite_task = self._rewrite.execute(
            CandidateProfile(
                job_role=job_request.job_role,
                experience_level=job_request.experience_level,
                # 기술 면접관의 말투를 어조로 넘긴다. 리텍스팅 대상이 이 면접관 질문뿐이라
                # solo 와 똑같이 프로필 하나로 충분하다.
                persona_type=job_request.tech_persona.style,
            ),
            job_request.questions,
        )
        generated, rewritten = await asyncio.gather(generate_task, rewrite_task, return_exceptions=True)

        if isinstance(generated, BaseException):
            raise generated
        if isinstance(rewritten, BaseException):
            raise rewritten
        if rewritten is None:
            # solo 는 여기서 원질문으로 폴백하지만 N:1 은 실패로 처리한다.
            # 질문 6개 중 4개가 신규 생성분이라, 어조만 어긋난 채 여는 것보다
            # 재시도하게 하는 편이 예측 가능하다.
            raise PipelineError(500, "기술 면접관 질문을 다듬지 못했습니다.")
        return generated, rewritten

    def _assemble(
        self,
        job_request: MultiTailorRequest,
        generated: tuple[GeneratedQuestion, ...],
        rewritten: tuple[TailoredQuestion, ...],
    ) -> list[MultiTailoredQuestion]:
        original_by_id = {question.id: question for question in job_request.questions}
        rewritten_by_id = {question.id: question.question for question in rewritten}

        # 기술 면접관 질문이 먼저다. id·카테고리·근거는 원질문 값을 유지하고 본문만 다시 쓴 것이다.
        questions = [
            MultiTailoredQuestion(
                id=original.id,
                persona_id=job_request.tech_persona.persona_id,
                category=original.category,
                question=rewritten_by_id.get(original.id, original.question),
                expected_answer=original.expected_answer,
                based_on=original.based_on,
            )
            for original in job_request.questions
        ]

        next_id = max(NEW_QUESTION_ID_START, max(original_by_id) + 1)
        for offset, question in enumerate(generated):
            questions.append(
                MultiTailoredQuestion(
                    id=next_id + offset,
                    persona_id=question.persona_id,
                    category=question.category,
                    question=question.question,
                    expected_answer=question.expected_answer,
                    based_on=question.based_on,
                )
            )
        return questions


def _failure_payload(job_id: str, interview_id: str, status_code: int, message: str) -> dict[str, Any]:
    return MultiTailorCallbackFailure(
        job_id=job_id,
        interview_id=interview_id,
        error=MultiTailorErrorDetail(status_code=status_code, message=message),
    ).model_dump(by_alias=True)
