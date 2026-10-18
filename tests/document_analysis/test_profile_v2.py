from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from app.core.common.applicant_profile.build_v2 import (
    DocumentProfileBuild,
    NondevFileReader,
    UnreadableDocumentError,
    filter_nondev_tree,
)
from app.core.common.applicant_profile.dto_v2 import DraftProfileV2
from app.core.common.applicant_profile.refine import InsufficientEvidenceError
from app.core.common.applicant_profile.refine_v2 import ProfileEvidenceRefineV2
from app.core.common.applicant_profile.summary import to_project_summary
from app.core.common.interview_qa.dto import RepoTree, Stage3Result
from app.core.common.interview_qa.stage1_validation import Stage1Validation
from app.core.common.interview_qa.stage2_nondev.dto import (
    DocumentReading,
    EvidenceBlock,
    ExtractedDocument,
    ExtractedPage,
    ProfileDocument,
)
from app.core.common.interview_qa.stage2_nondev.extract import DocumentExtract
from app.core.common.interview_qa.stage2_nondev.page_reader import PageReader
from app.core.common.interview_qa.stage2_pdf_extract import Stage2PdfExtract
from app.core.common.interview_qa.stage3_repo_tree import Stage3RepoTree
from app.core.common.job_spec import JOBS
from tests.document_analysis.helpers import DocumentClient, Fetcher, pdf_bytes

QUOTE = "테스트 설계에서 경계값과 예외를 검증했습니다."
BODY = QUOTE + "\n결함 보고서에 재현 절차와 기대 결과를 기록했습니다. 프로젝트의 범위와 결과를 검토했습니다."


def draft(path: str = "doc-1/p1") -> DraftProfileV2:
    return DraftProfileV2.model_validate(
        {
            "overview": "테스트 경험",
            "repositories": [],
            "claim_checks": [],
            "materials": [
                {
                    "category": category,
                    "title": category,
                    "summary": "검증 사례",
                    "evidence": [{"path": path, "quote": QUOTE}],
                }
                for category in ("test_design", "defect_reporting")
            ],
        }
    )


def builder(client: DocumentClient, data: bytes) -> DocumentProfileBuild:
    fetcher = Fetcher(data)
    stage1 = Stage1Validation(fetcher, Mock())
    return DocumentProfileBuild(
        client,
        fetcher,
        DocumentExtract(Stage2PdfExtract(0.5, 3)),
        PageReader(client),
        stage1,
        Mock(spec=Stage3RepoTree),
        NondevFileReader(60000, 12),
        "stub",
    )


@pytest.mark.parametrize("method", ["TEXT", "TABLE", "VISION_TRANSCRIPT"])
def test_valid_quote_assigns_server_source_kind(method: str) -> None:
    block = EvidenceBlock("block", "doc-1/p1", (0, 0, 1, 1), method, BODY)  # type: ignore[arg-type]
    result = ProfileEvidenceRefineV2().execute(draft(), JOBS["QA_ENGINEER"], [block], [], {})
    assert {material.source_kind for material in result.materials} == (
        {"VISION"} if method == "VISION_TRANSCRIPT" else {"DOCUMENT"}
    )
    assert result.schema_version == 2
    summary = to_project_summary(result)
    assert summary.tech_stack == []
    assert summary.core_features[0].based_on == ["doc-1/p1"]


@pytest.mark.parametrize("quote", ["없던 내용의 수치를 모델이 새로 만들었다", "너무 짧음", "가" * 81])
def test_invalid_quotes_are_removed_not_promoted(quote: str) -> None:
    value = draft()
    for material in value.materials:
        material.evidence[0].quote = quote
    with pytest.raises(InsufficientEvidenceError):
        ProfileEvidenceRefineV2().execute(
            value, JOBS["QA_ENGINEER"], [EvidenceBlock("b", "doc-1/p1", (0, 0, 1, 1), "TEXT", BODY)], [], {}
        )


def test_summary_and_relation_only_content_cannot_support_questions() -> None:
    with pytest.raises(InsufficientEvidenceError):
        ProfileEvidenceRefineV2().execute(
            draft(),
            JOBS["QA_ENGINEER"],
            [EvidenceBlock("b", "doc-1/p1", (0, 0, 1, 1), "VISION_RELATION", BODY)],
            [],
            {},
        )


def test_document_priority_and_supplement_only_rejection() -> None:
    blocks = [
        EvidenceBlock("v", "doc-1/p1", (0, 0, 1, 1), "VISION_TRANSCRIPT", BODY),
        EvidenceBlock("t", "doc-1/p1", (0, 0, 1, 1), "TEXT", BODY),
    ]
    result = ProfileEvidenceRefineV2().execute(draft(), JOBS["QA_ENGINEER"], blocks, [], {})
    assert all(material.source_kind == "DOCUMENT" for material in result.materials)
    with pytest.raises(InsufficientEvidenceError):
        ProfileEvidenceRefineV2().execute(
            draft("supp-1/goal"),
            JOBS["QA_ENGINEER"],
            [
                EvidenceBlock("s", "supp-1/goal", (0, 0, 0, 0), "TEXT", BODY),
            ],
            [],
            {},
        )


async def test_document_only_profile_uses_original_quote_and_never_reopens_pdf_for_summary() -> None:
    client = DocumentClient(profile=draft().model_dump())
    result = await builder(client, pdf_bytes(BODY)).execute(
        JOBS["QA_ENGINEER"], (ProfileDocument(type="TEST_PLAN", url="https://example.com/plan.pdf"),), (), ()
    )
    assert result.sources[0].pages == 1
    assert result.materials[0].source_kind == "DOCUMENT"
    assert len(client.calls) == 1
    assert client.calls[0]["tool_choice"]["name"] == "submit_profile_v2"
    assert client.count_calls
    before = len(client.calls)
    to_project_summary(result)
    assert len(client.calls) == before


async def test_unreadable_required_document_stops_before_profile_generation() -> None:
    client = DocumentClient(profile=draft().model_dump())
    with pytest.raises(UnreadableDocumentError):
        await builder(client, pdf_bytes("")).execute(
            JOBS["QA_ENGINEER"], (ProfileDocument(type="TEST_PLAN", url="https://example.com/plan.pdf"),), (), ()
        )
    assert client.calls == []
    assert client.count_calls == []


def test_required_document_with_only_reading_summary_is_unreadable() -> None:
    page = ExtractedPage(number=1, kind="E", bbox=(0, 0, 100, 100), blocks=[], tables=[], regions=[])
    document = ExtractedDocument(id="doc-1", type="TEST_PLAN", title=None, pdf=b"", pages=[page])
    reading = DocumentReading(documents=[document], summaries={"doc-1/p1": ["테스트 계획 구성도로 보임"]})
    with pytest.raises(UnreadableDocumentError):
        DocumentProfileBuild._ensure_readable(reading, JOBS["QA_ENGINEER"])


def test_nondev_code_reader_excludes_data_and_notebook_outputs(tmp_path: Path) -> None:
    reader = NondevFileReader(60000, 12)
    data = tmp_path / "data.csv"
    data.write_text("secret,data")
    notebook = tmp_path / "work.ipynb"
    notebook.write_text('{"cells":[{"cell_type":"code","source":["print(42)"],"outputs":["customer_secret"]}]}')
    result = reader.read_files(
        ["repo/data.csv", "repo/work.ipynb"], {"repo/data.csv": str(data), "repo/work.ipynb": str(notebook)}, set()
    )
    assert "repo/data.csv" not in result["files"]
    assert "print(42)" in result["files"]["repo/work.ipynb"]["content"]
    assert "customer_secret" not in str(result)


def test_sensitive_files_hidden_and_data_visible_but_not_readable(tmp_path: Path) -> None:
    paths = [".env.production", "terraform.tfstate", "secret.pem", "id_rsa.pub", "data.csv", "work.py"]
    tree = Stage3Result(
        repos=[RepoTree(name="app", role="unknown", file_paths=paths)],
        tree_text="ignored",
        path_index={f"app/{path}": str(tmp_path / path) for path in paths},
    )
    filtered = filter_nondev_tree(tree)
    assert filtered.repos[0].file_paths == ["data.csv", "work.py"]
    assert list(filtered.path_index) == ["app/work.py"]
    assert "secret" not in filtered.tree_text


async def test_budget_drops_optional_documents_before_required_and_records_pages() -> None:
    class BudgetClient(DocumentClient):
        async def count_tokens(self, **kwargs: object) -> int:
            messages = kwargs["messages"]
            assert isinstance(messages, list)
            text = messages[0]["content"]
            self.count_calls.append({"text": text})
            return 2000 if "[doc-1 ·" in text or "[doc-2 ·" in text else 100

    client = BudgetClient(profile=draft("doc-3/p1").model_dump())
    pipeline = builder(client, pdf_bytes(BODY))
    pipeline.input_token_limit = 1000
    result = await pipeline.execute(
        JOBS["QA_ENGINEER"],
        tuple(
            ProfileDocument(type=kind, url=f"https://example.com/{kind}.pdf")
            for kind in ("RESUME", "CAREER_DESCRIPTION", "TEST_PLAN")
        ),
        (),
        (),
    )
    assert [source.dropped_pages for source in result.sources] == [[1], [1], []]
    assert "[doc-1 ·" not in client.calls[0]["messages"][0]["content"]
    assert all(material.evidence[0].path == "doc-3/p1" for material in result.materials)
