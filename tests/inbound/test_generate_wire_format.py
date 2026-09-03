from app.core.common.interview_qa.dto import (
    CallbackErrorDetail,
    CallbackFailure,
    CallbackSuccess,
    InterviewQaResult,
)
from app.inbound.http.interview_qa.dto import GenerateRequest, JobAccepted
from app.inbound.http.interview_qa.mock_payload import MOCK_RESULT

_RAW_LLM_RESULT = {
    # Stage 4 tool-use 스키마는 snake_case 다. 검증은 그대로 통과해야 한다.
    "project_summary": {"overview": "o", "repositories": [], "core_features": [], "tech_stack": []},
    "interview": [
        {
            "id": index,
            "category": "tech_choice",
            "question": "q",
            "expected_answer": "a",
            "based_on": ["repo/src/file.py"],
        }
        for index in range(1, 6)
    ],
}


def test_generate_request_accepts_camel_and_snake_case() -> None:
    camel = {
        "portfolioUrl": "https://example.com/p.pdf",
        "githubUrls": ["https://github.com/owner/repo"],
        "callbackUrl": "https://example.com/cb",
    }
    snake = {
        "portfolio_url": "https://example.com/p.pdf",
        "github_urls": ["https://github.com/owner/repo"],
        "callback_url": "https://example.com/cb",
    }
    assert GenerateRequest.model_validate(camel) == GenerateRequest.model_validate(snake)


def test_generate_accepted_response_is_camel_case() -> None:
    assert JobAccepted(job_id="j-1").model_dump(by_alias=True)["jobId"] == "j-1"


def test_generate_success_callback_is_camel_case() -> None:
    result = InterviewQaResult.model_validate(_RAW_LLM_RESULT)
    payload = CallbackSuccess(job_id="j-1", result=result).model_dump(by_alias=True)

    assert payload["jobId"] == "j-1"
    assert set(payload["result"]) == {"projectSummary", "interview"}
    assert {"coreFeatures", "techStack"} <= set(payload["result"]["projectSummary"])
    assert {"expectedAnswer", "basedOn"} <= set(payload["result"]["interview"][0])


def test_generate_failure_callback_is_camel_case() -> None:
    payload = CallbackFailure(
        job_id="j-1",
        error=CallbackErrorDetail(status_code=422, message="m"),
    ).model_dump(by_alias=True)

    assert payload["jobId"] == "j-1"
    assert payload["error"]["statusCode"] == 422


def test_mock_payload_matches_result_schema() -> None:
    # 모킹 콜백이 실제 성공 콜백과 다른 모양으로 흘러가지 않게 고정한다.
    assert InterviewQaResult.model_validate(MOCK_RESULT)
