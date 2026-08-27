from fastapi import APIRouter, status

from app.core.common.feedback.dto import FeedbackCallbackFailure
from app.core.common.feedback.solo.dto import FeedbackCallbackSuccess

# 콜백은 우리가 "보내는" 쪽이라 이 라우터가 실제로 서빙되지는 않는다.
feedback_solo_callback_router = APIRouter()


@feedback_solo_callback_router.post(
    "{$request.body#/callbackUrl}",
    summary="1:1 답변 피드백 결과 콜백",
    status_code=status.HTTP_200_OK,
)
def feedback_solo_callback(
    body: FeedbackCallbackSuccess | FeedbackCallbackFailure,
) -> None:
    """채점이 끝나면 요청의 ``callbackUrl`` 로 이 페이로드를 POST 한다.

    ``result.feedbacks`` 에는 답변이 있는 문항만 담긴다 — 미답변 문항은 빠지고
    ``overall.answeredCount`` / ``overall.questionCount`` 에만 반영된다.
    """
