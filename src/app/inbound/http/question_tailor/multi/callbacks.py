from fastapi import APIRouter, status

from app.core.common.question_tailor.multi.dto import (
    MultiTailorCallbackFailure,
    MultiTailorCallbackSuccess,
)

# 콜백은 우리가 "보내는" 쪽이라 이 라우터가 실제로 서빙되지는 않는다.
question_tailor_multi_callback_router = APIRouter()


@question_tailor_multi_callback_router.post(
    "{$request.body#/callbackUrl}",
    summary="N:1 질문 구성 결과 콜백",
    status_code=status.HTTP_200_OK,
)
def question_tailor_multi_callback(
    body: MultiTailorCallbackSuccess | MultiTailorCallbackFailure,
) -> None:
    """작업이 끝나면 요청의 ``callbackUrl`` 로 이 페이로드를 POST 한다.

    ``result.tailored`` 가 false 면 기술 질문 리텍스팅에 실패해 원질문을 그대로 쓴 것이다.
    이때도 status 는 succeeded 다 — 비개발 질문은 생성됐으므로 면접을 열 수 있다.
    비개발 질문 생성이 실패하면 대신 쓸 것이 없어 status 는 failed 로 온다.
    ``result.questions`` 는 면접 진행 순서대로 담긴다(기술 면접관 몫이 먼저).
    """
