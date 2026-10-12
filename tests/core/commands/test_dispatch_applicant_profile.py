from typing import Any, cast

from app.core.commands.dispatch_applicant_profile import DispatchApplicantProfile
from app.core.common.applicant_profile.dto import ProfileJobRequest
from app.core.common.applicant_profile.exploration import ProfileExploration
from app.core.common.applicant_profile.refine import ProfileEvidenceRefine
from app.core.common.interview_qa.dto import MergedDocument, Stage1Result, Stage3Result
from app.core.common.interview_qa.errors import PipelineError
from app.core.common.interview_qa.stage1_validation import Stage1Validation
from app.core.common.interview_qa.stage2_document_merge import Stage2DocumentMerge
from app.core.common.interview_qa.stage2_image_llm_triage import Stage2ImageLlmTriage
from app.core.common.interview_qa.stage2_image_structuring import Stage2ImageStructuring
from app.core.common.interview_qa.stage2_image_triage import Stage2ImageTriage
from app.core.common.interview_qa.stage2_pdf_extract import Stage2PdfExtract
from app.core.common.interview_qa.stage3_repo_tree import Stage3RepoTree
from app.core.common.interview_qa.stage4_file_reader import Stage4FileReader
from tests.core.common.applicant_profile.test_exploration import ScriptedClient
from tests.multi_helpers import RecordingWebhook
from tests.profile_helpers import PATH_INDEX, raw_profile


class _Stage1:
    def __init__(self, error: PipelineError | None = None) -> None:
        self.error = error

    async def execute(self, portfolio_url: str, github_urls: tuple[str, ...]) -> Stage1Result:
        if self.error is not None:
            raise self.error
        return Stage1Result(pdf_bytes=b"%PDF", repos=())


class _Stage2:
    # Stage 2 의 다섯 단계는 디스패처가 결과를 다음 단계로 넘기기만 하므로 하나로 흉내 낸다.
    async def execute(self, _: object) -> MergedDocument:
        return MergedDocument(portfolio_text="재고 서비스 포트폴리오", branch="text_heavy")


class _Stage3:
    async def execute(self, repos: object, working_dir: str) -> Stage3Result:
        return Stage3Result(repos=[], tree_text="order-api/build.gradle", path_index=PATH_INDEX)


def _dispatcher(submission: dict[str, Any], stage1: _Stage1 | None = None) -> tuple[Any, RecordingWebhook]:
    webhook = RecordingWebhook()
    client = ScriptedClient([[{"type": "tool_use", "id": "t-1", "name": "submit_profile", "input": submission}]])
    stage2 = _Stage2()
    dispatcher = DispatchApplicantProfile(
        webhook=webhook,
        stage1_validation=cast("Stage1Validation", stage1 or _Stage1()),
        stage2_pdf_extract=cast("Stage2PdfExtract", stage2),
        stage2_image_triage=cast("Stage2ImageTriage", stage2),
        stage2_image_llm_triage=cast("Stage2ImageLlmTriage", stage2),
        stage2_image_structuring=cast("Stage2ImageStructuring", stage2),
        stage2_document_merge=cast("Stage2DocumentMerge", stage2),
        stage3_repo_tree=cast("Stage3RepoTree", _Stage3()),
        profile_exploration=ProfileExploration(
            client, Stage4FileReader(max_file_bytes=60_000, max_files_per_call=12), "stub", 8, 100_000, 8192
        ),
        profile_refine=ProfileEvidenceRefine(),
    )
    return dispatcher, webhook


def _request() -> ProfileJobRequest:
    return ProfileJobRequest(
        major="컴퓨터공학",
        portfolio_url="https://example.com/p.pdf",
        github_urls=("https://github.com/o/order-api",),
        callback_url="https://example.com/cb",
    )


async def test_success_callback_carries_profile_and_project_summary() -> None:
    dispatcher, webhook = _dispatcher(raw_profile())

    await dispatcher.execute("j-1", _request())

    (payload,) = webhook.payloads
    assert payload["jobId"] == "j-1"
    assert payload["status"] == "succeeded"
    assert set(payload["result"]) == {"profile", "projectSummary"}
    assert payload["result"]["profile"]["major"] == "컴퓨터공학"
    assert payload["result"]["projectSummary"]["techStack"] == ["Spring Boot", "Redis"]


async def test_insufficient_evidence_sends_422_failure_callback() -> None:
    submission = raw_profile()
    for key in ("tech_stack", "troubleshootings", "integrations", "structure_notes"):
        submission[key] = []
    dispatcher, webhook = _dispatcher(submission)

    await dispatcher.execute("j-1", _request())

    (payload,) = webhook.payloads
    assert payload["status"] == "failed"
    assert payload["error"]["statusCode"] == 422


async def test_stage1_error_is_passed_through() -> None:
    dispatcher, webhook = _dispatcher(raw_profile(), _Stage1(PipelineError(403, "private 저장소")))

    await dispatcher.execute("j-1", _request())

    (payload,) = webhook.payloads
    assert payload["error"] == {"statusCode": 403, "message": "private 저장소"}
