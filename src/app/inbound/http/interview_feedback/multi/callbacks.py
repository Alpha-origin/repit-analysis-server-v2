from fastapi import APIRouter, status

from app.core.common.feedback.dto import FeedbackCallbackFailure
from app.core.common.feedback.multi.dto import MultiFeedbackCallbackSuccess

# 콜백은 우리가 "보내는" 쪽이라 이 라우터가 실제로 서빙되지는 않는다.
feedback_multi_callback_router = APIRouter()


@feedback_multi_callback_router.post(
    "{$request.body#/callbackUrl}",
    summary="N:1 답변 피드백 결과 콜백",
    status_code=status.HTTP_200_OK,
)
def feedback_multi_callback(
    body: MultiFeedbackCallbackSuccess | FeedbackCallbackFailure,
) -> None:
    """채점이 끝나면 요청의 ``callbackUrl`` 로 이 페이로드를 POST 한다.

    1:1 의 2계층(overall + feedbacks) 에 ``result.personas`` 가 하나 더 얹힌다.
    담당 문항이 하나도 없던 면접관도 personas 에는 반드시 들어간다(score 0).
    """
