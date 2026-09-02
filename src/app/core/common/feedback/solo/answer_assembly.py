from __future__ import annotations

import logging
from collections.abc import Sequence

from app.core.common.feedback.solo.dto import (
    AssembledSession,
    FeedbackAnswer,
    FeedbackQuestion,
    FeedbackSoloRequest,
    GradingTarget,
)
from app.core.common.interview_qa.errors import PipelineError

logger = logging.getLogger(__name__)


class AnswerAssembly:
    # 외부 의존이 없는 순수 로직이라 동기 메서드로 둔다.
    # (interview_qa 의 stage 들은 async 지만, 여기서 async 를 흉내내면 await 가 거짓 정보가 된다.)
    def execute(self, request: FeedbackSoloRequest) -> AssembledSession:
        return self.assemble(request.questions, request.answers, session_id=request.session_id)

    def assemble(
        self,
        questions: Sequence[FeedbackQuestion],
        answers: Sequence[FeedbackAnswer],
        *,
        session_id: str,
    ) -> AssembledSession:
        """질문 x 답변을 조인하고 부모 맥락을 붙인다. 미답변은 채점 대상에서 분리한다.

        요청 DTO 가 아니라 재료만 받는 이유는 N:1 채점도 같은 조립을 쓰기 때문이다.
        N:1 은 질문에 personaId 가 붙을 뿐 체인 구조가 1:1 과 같다.
        """
        # 같은 question_id 로 답변이 여러 개 오면 마지막 것이 유효 답변(재답변 시나리오).
        answer_map = {answer.question_id: answer.content for answer in answers}
        question_map = {question.question_id: question for question in questions}

        targets: list[GradingTarget] = []
        unanswered: list[str] = []

        for question in questions:
            answer = answer_map.get(question.question_id)
            # 공백뿐인 답변도 미답변으로 본다. 채점하면 "못한 것"과 "안 한 것"이 섞인다.
            if answer is None or not answer.strip():
                unanswered.append(question.question_id)
                continue

            parent_question, parent_answer = self._resolve_parent(question, question_map, answer_map)
            targets.append(
                GradingTarget(
                    question_id=question.question_id,
                    type=question.type,
                    intention=question.intention,
                    content=question.content,
                    answer=answer,
                    parent_question=parent_question,
                    parent_answer=parent_answer,
                )
            )

        # 전 문항 미답변이면 채점할 것이 없다. 빈 결과를 성공으로 내보내면 0점과 구분되지 않는다.
        if not targets:
            raise PipelineError(422, "채점할 답변이 없습니다.")

        logger.info(
            "feedback_solo.assembly.done",
            extra={
                "session_id": session_id,
                "question_count": len(questions),
                "answered_count": len(targets),
                "unanswered_count": len(unanswered),
            },
        )
        return AssembledSession(
            targets=tuple(targets),
            unanswered_question_ids=tuple(unanswered),
            question_count=len(questions),
        )

    @staticmethod
    def _resolve_parent(
        question: FeedbackQuestion,
        question_map: dict[str, FeedbackQuestion],
        answer_map: dict[str, str],
    ) -> tuple[str | None, str | None]:
        if question.type != "FOLLOW" or question.parent_id is None:
            return None, None

        parent = question_map.get(question.parent_id)
        if parent is None:
            # 데이터 정합성 문제지만 채점 전체를 막을 이유는 없다 — 맥락 없이 진행.
            logger.warning(
                "feedback_solo.assembly.parent_not_found",
                extra={"question_id": question.question_id, "parent_id": question.parent_id},
            )
            return None, None

        return parent.content, answer_map.get(question.parent_id)
