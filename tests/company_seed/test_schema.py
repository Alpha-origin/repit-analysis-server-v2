from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from company_seed.schema import Package, export_bundle, validate_bundle

ROOT = Path(__file__).resolve().parents[2] / "company_seed"
TODAY = date(2026, 10, 17)


@pytest.fixture
def payload() -> dict[str, Any]:
    return json.loads((ROOT / "data/drafts/naver.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def write_package(directory: Path, payload: dict[str, Any]) -> None:
    (directory / f"{payload['code']}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_pilot_matches_confirmed_inventory() -> None:
    with (ROOT / "companies.csv").open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == 26
    assert len({row["code"] for row in rows}) == 26
    pilots = {row["code"] for row in rows if row["pilot"] == "true"}
    packages = validate_bundle(ROOT / "data/drafts", reviewed=False, today=TODAY)
    assert pilots <= {package.code for package in packages} <= {row["code"] for row in rows}
    assert all(package.review.reviewed_by is None for package in packages)
    assert all((ROOT / "data/review" / f"{package.code}.md").is_file() for package in packages)


@pytest.mark.parametrize("reviewed", [False, True])
def test_empty_bundle_fails(tmp_path: Path, reviewed: bool) -> None:
    with pytest.raises(ValueError, match="패키지가 없습니다"):
        validate_bundle(tmp_path, reviewed=reviewed, today=TODAY)


def test_drafts_cannot_be_exported(tmp_path: Path) -> None:
    destination = tmp_path / "export"
    with pytest.raises(ValueError, match="사람 검수"):
        export_bundle(ROOT / "data/drafts", destination, today=TODAY)
    assert not destination.exists()


def test_export_round_trip_and_no_overwrite(tmp_path: Path, payload: dict[str, Any]) -> None:
    source = tmp_path / "reviewed"
    source.mkdir()
    # Synthetic reviewer only in a temporary fixture, never in committed real company data.
    payload["review"] = {"reviewedBy": "테스트 검수자", "reviewedAt": TODAY.isoformat()}
    write_package(source, payload)
    destination = tmp_path / "export"
    export_bundle(source, destination, today=TODAY)
    assert validate_bundle(destination, reviewed=True, today=TODAY) == validate_bundle(
        source, reviewed=True, today=TODAY
    )
    original = (destination / "naver.json").read_bytes()
    with pytest.raises(FileExistsError):
        export_bundle(source, destination, today=TODAY)
    assert (destination / "naver.json").read_bytes() == original


@pytest.mark.parametrize(
    ("field", "value"),
    [("displayName", "가" * 31), ("interviewerBrief", "가" * 601), ("schemaVersion", 2), ("code", "../bad")],
)
def test_contract_limits(payload: dict[str, Any], field: str, value: object) -> None:
    payload[field] = value
    with pytest.raises(ValueError, match=r".+"):
        Package.model_validate(payload)


@pytest.mark.parametrize("mutation", ["unlinked", "duplicate", "forbidden", "signals", "signal-origin", "size"])
def test_semantic_contract(payload: dict[str, Any], mutation: str) -> None:
    if mutation == "unlinked":
        payload["facts"][0]["sourceKey"] = "missing"
    elif mutation == "duplicate":
        payload["sources"].append(payload["sources"][0].copy())
    elif mutation == "forbidden":
        payload["facts"][0]["content"] = "우리 회사의 기출"
    elif mutation == "signals":
        payload["facts"][0]["positiveSignals"] = ["허용되지 않는 신호"]
    elif mutation == "signal-origin":
        payload["facts"][-1].pop("signalsOrigin")
    else:
        payload["legalSize"] = "LARGE"
    with pytest.raises(ValueError, match=r".+"):
        Package.model_validate(payload)


@pytest.mark.parametrize(
    "mutation", ["sample", "business-count", "future", "published-future", "review-before", "name"]
)
def test_authoring_rules(tmp_path: Path, payload: dict[str, Any], mutation: str) -> None:
    if mutation == "sample":
        payload["sources"][0]["sourceType"] = "SAMPLE"
    elif mutation == "business-count":
        payload["facts"].pop(0)
    elif mutation == "future":
        payload["sources"][0]["collectedAt"] = "2026-10-18"
        payload["dataAsOf"] = "2026-10-18"
    elif mutation == "published-future":
        payload["sources"][0]["publishedAt"] = "2026-10-18"
    elif mutation == "review-before":
        payload["review"] = {"reviewedBy": "테스트", "reviewedAt": "2026-10-16"}
    write_package(tmp_path, payload)
    if mutation == "name":
        (tmp_path / "naver.json").rename(tmp_path / "wrong.json")
    with pytest.raises(ValueError, match=r".+"):
        validate_bundle(tmp_path, reviewed=False, today=TODAY)


@pytest.mark.parametrize("mutation", ["duplicate-id", "missing-parent", "cycle"])
def test_bundle_links(tmp_path: Path, payload: dict[str, Any], mutation: str) -> None:
    other = json.loads(json.dumps(payload))
    other["code"] = "other"
    if mutation == "duplicate-id":
        payload["businessRegNo"] = other["businessRegNo"] = "1234567890"
    elif mutation == "missing-parent":
        payload["parentCode"] = "missing"
    else:
        payload["parentCode"] = "other"
        other["parentCode"] = "naver"
    write_package(tmp_path, payload)
    write_package(tmp_path, other)
    with pytest.raises(ValueError, match=r".+"):
        validate_bundle(tmp_path, reviewed=False, today=TODAY)
