from fastapi import APIRouter, status

from app.core.common.question_tailor.dto import (
    QuestionTailorCallbackFailure,
    QuestionTailorCallbackSuccess,
)

# 콜백은 우리가 "보내는" 쪽이라 이 라우터가 실제로 서빙되지는 않는다.
question_tailor_callback_router = APIRouter()


@question_tailor_callback_router.post(
    "{$request.body#/callbackUrl}",
    summary="질문 재작성 결과 콜백",
    status_code=status.HTTP_200_OK,
)
def question_tailor_callback(
    body: QuestionTailorCallbackSuccess | QuestionTailorCallbackFailure,
) -> None:
    """작업이 끝나면 요청의 ``callbackUrl`` 로 이 페이로드를 POST 한다.

    면접 시작 전이라 세션이 없다 — 수신측은 ``interviewId`` 로 매칭한다.
    ``result.tailored`` 가 false 면 재작성에 실패해 원질문을 그대로 돌려준 것이다.
    이때도 status 는 succeeded 다(원질문으로 면접을 열 수 있으므로).
    """
