from fastapi import APIRouter, status

from app.core.common.interview_qa.dto import CallbackFailure, CallbackSuccess

# 콜백은 우리가 "보내는" 쪽이라 이 라우터가 실제로 서빙되지는 않는다.
# OpenAPI 의 callbacks 절에만 실려서, 수신측이 필드 이름을 추측하지 않게 하는 것이 목적이다.
interview_qa_callback_router = APIRouter()


@interview_qa_callback_router.post(
    "{$request.body#/callbackUrl}",
    summary="면접 Q&A 생성 결과 콜백",
    status_code=status.HTTP_200_OK,
)
def interview_qa_callback(body: CallbackSuccess | CallbackFailure) -> None:
    """작업이 끝나면 요청의 ``callbackUrl`` 로 이 페이로드를 POST 한다.

    콜백 5종 모두 camelCase 다(``jobId`` / ``statusCode``).
    2xx 응답이면 성공으로 보고, 아니면 지수 백오프로 몇 번 더 보낸다.
    """


# 모킹 엔드포인트용. 페이로드는 위와 같지만, 라우터를 공유하면 operationId 가 겹친다.
interview_qa_mock_callback_router = APIRouter()


@interview_qa_mock_callback_router.post(
    "{$request.body#/callbackUrl}",
    summary="면접 Q&A 생성 결과 콜백(모킹)",
    status_code=status.HTTP_200_OK,
)
def interview_qa_mock_callback(body: CallbackSuccess | CallbackFailure) -> None:
    """고정된 모킹 결과를 지연 후 ``callbackUrl`` 로 POST 한다. 형식은 실제 콜백과 같다."""
