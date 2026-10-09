import pytest

from app.core.common.applicant_profile.dto import ProfileResult
from app.core.common.applicant_profile.evidence import available_categories, evidence_paths
from app.core.common.applicant_profile.refine import ProfileEvidenceRefine
from app.core.common.applicant_profile.summary import to_project_summary
from app.core.common.interview_qa.errors import PipelineError
from tests.profile_helpers import PATH_INDEX, evidence, raw_profile


def test_valid_profile_keeps_every_category_and_fills_server_fields() -> None:
    profile = ProfileEvidenceRefine().execute(raw_profile(), PATH_INDEX, "컴퓨터공학")

    assert profile.schema_version == 1
    assert profile.major == "컴퓨터공학"
    assert available_categories(profile) == (
        "tech_choice",
        "implementation",
        "troubleshooting",
        "integration",
        "structure",
    )
    # 같은 경로의 근거는 하나로 합친다.
    assert [e.path for e in profile.core_features[0].evidence] == ["order-api/src/main/java/order/StockService.java"]


def test_directory_key_path_is_accepted_and_trailing_slash_removed() -> None:
    profile = ProfileEvidenceRefine().execute(raw_profile(), PATH_INDEX, None)

    assert profile.structure_notes[0].key_paths == ["order-api/src/main/java/order"]
    assert "order-api/src/main/java/order" in evidence_paths(profile)


@pytest.mark.parametrize(
    "bad_path",
    [
        "order-api",  # 저장소 이름만 있는 경로
        "order-api/",  # 끝 슬래시를 지우면 저장소 이름만 남는다
        "order-api/src/Missing.java",  # 트리에 없는 파일
        "order-api//src",  # 빈 세그먼트
    ],
)
def test_invalid_paths_are_removed(bad_path: str) -> None:
    raw = raw_profile()
    raw["tech_stack"][0]["evidence"].append(evidence(bad_path))

    profile = ProfileEvidenceRefine().execute(raw, PATH_INDEX, None)

    assert [e.path for e in profile.tech_stack[0].evidence] == ["order-api/build.gradle"]


def test_items_without_evidence_are_dropped() -> None:
    raw = raw_profile()
    raw["tech_stack"].append({"name": "Kafka", "evidence": [evidence("order-api/src/Kafka.java")]})
    raw["core_features"].append({"name": "추천", "description": "d", "implementation": "", "evidence": []})
    raw["integrations"].append({"from": "a", "to": "b", "method": "", "evidence": [evidence("order-api")]})
    raw["structure_notes"].append({"repo": "order-web", "summary": "s", "key_paths": ["order-web"]})

    profile = ProfileEvidenceRefine().execute(raw, PATH_INDEX, None)

    assert [item.name for item in profile.tech_stack] == ["Spring Boot", "Redis"]
    assert [item.name for item in profile.core_features] == ["재고 차감"]
    assert len(profile.integrations) == 1
    assert [note.repo for note in profile.structure_notes] == ["order-api"]


def test_unverified_troubleshooting_moves_to_claim_checks() -> None:
    raw = raw_profile()
    raw["troubleshootings"].append(
        {
            "title": "메모리 누수",
            "portfolio_claim": "배치에서 OOM 이 났다",
            "resolution_in_code": "",
            "evidence": [evidence("order-api/src/Batch.java")],
        }
    )

    profile = ProfileEvidenceRefine().execute(raw, PATH_INDEX, None)

    assert [item.title for item in profile.troubleshootings] == ["재고 음수"]
    moved = profile.claim_checks[-1]
    assert moved.claim == "메모리 누수: 배치에서 OOM 이 났다"
    assert moved.status == "unverified"


def test_fewer_than_two_categories_fails_with_422() -> None:
    raw = raw_profile()
    for key in ("tech_stack", "troubleshootings", "integrations", "structure_notes"):
        raw[key] = []

    with pytest.raises(PipelineError) as exc_info:
        ProfileEvidenceRefine().execute(raw, PATH_INDEX, None)
    assert exc_info.value.status_code == 422


def test_no_evidence_at_all_fails_with_422() -> None:
    with pytest.raises(PipelineError) as exc_info:
        ProfileEvidenceRefine().execute(raw_profile(), {}, None)
    assert exc_info.value.status_code == 422


def test_malformed_submission_fails_with_500() -> None:
    raw = raw_profile()
    del raw["overview"]

    with pytest.raises(PipelineError) as exc_info:
        ProfileEvidenceRefine().execute(raw, PATH_INDEX, None)
    assert exc_info.value.status_code == 500


def test_project_summary_is_copied_from_profile() -> None:
    profile = ProfileEvidenceRefine().execute(raw_profile(), PATH_INDEX, None)

    summary = to_project_summary(profile)

    assert summary.overview == "주문·재고 서비스"
    assert summary.tech_stack == ["Spring Boot", "Redis"]
    repository = summary.repositories[0]
    assert (repository.repo, repository.role, repository.description) == ("order-api", "api_server", "주문 API")
    assert summary.core_features[0].based_on == ["order-api/src/main/java/order/StockService.java"]


def test_success_payload_is_camel_case_and_keeps_from_key() -> None:
    profile = ProfileEvidenceRefine().execute(raw_profile(), PATH_INDEX, None)

    payload = ProfileResult(profile=profile, project_summary=to_project_summary(profile)).model_dump(by_alias=True)

    assert {"schemaVersion", "techStack", "coreFeatures", "claimChecks", "structureNotes"} <= set(payload["profile"])
    assert payload["profile"]["integrations"][0]["from"] == "order-api"
    assert payload["profile"]["structureNotes"][0]["keyPaths"] == ["order-api/src/main/java/order"]
    assert "basedOn" in payload["projectSummary"]["coreFeatures"][0]
