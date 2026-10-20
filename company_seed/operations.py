"""Explicit human operations: diagnostics, manual evidence intake, and review declaration."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

from company_seed.collection import MAX_BYTES, CollectionReport, cached_documents, store_document
from company_seed.documents import CompanySeed, Seed, allowed_url, parse_document
from company_seed.rendering import browser_ready
from company_seed.schema import Package, check_authoring
from company_seed.storage import preserve_artifact, write_json


def doctor(companies: list[CompanySeed], model: str | None) -> dict[str, object]:
    return {
        "companies": len(companies),
        "sources": sum(len(c.sources) for c in companies),
        "model": model,
        "anthropicKeyPresent": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "dartKeyPresent": bool(os.environ.get("DART_API_KEY")),
        "chromiumReady": browser_ready(),
        "networkCalled": False,
        "modelCalled": False,
        "instructions": "기본 수집은 키 없이 가능. JS는 company 그룹/Chromium, 추출은 Anthropic 키와 모델 필요.",
    }


def import_evidence(company: CompanySeed, cache: Path, file: Path, url: str, scope: str, today: date) -> str:
    allowed_url(url, company.allowed_hosts)
    types = {
        ".pdf": "application/pdf",
        ".html": "text/html",
        ".htm": "text/html",
        ".txt": "text/plain",
        ".md": "text/plain",
    }
    if file.suffix.lower() not in types or file.stat().st_size > MAX_BYTES:
        raise ValueError("25MB 이하 PDF/HTML/TXT/MD 본문 파일만 반입 가능")
    registered = next((s for s in company.sources if s.url == url), None)
    seed = Seed(url=url, scope=scope, source_type=registered.source_type if registered else "ABOUT_PAGE")
    document, _ = parse_document(seed, file.read_bytes(), types[file.suffix.lower()], today)
    document.acquisition = "manual"
    folder = cache / company.code
    version, _ = store_document(folder, document)
    index = folder / "manual-index.json"
    names = json.loads(index.read_text(encoding="utf-8")) if index.exists() else []
    # Latest manually provided version of this URL replaces the previous pointer, preserving original snapshots.
    names = [name for name in names if not name.startswith(document.source.source_key + "-")]
    write_json(index, [*names, version])
    latest = folder / "latest.json"
    report: CollectionReport = (
        json.loads(latest.read_text(encoding="utf-8"))
        if latest.exists()
        else {
            "company": company.code,
            "checkedAt": today.isoformat(),
            "documents": [],
            "pages": [],
            "limitReached": False,
        }
    )
    report["documents"] = [
        name for name in report["documents"] if not name.startswith(document.source.source_key + "-")
    ]
    report["documents"].append(version)
    write_json(latest, report)
    return version


def approve(file: Path, destination: Path, reviewer: str, *, confirmed: bool, today: date) -> None:
    if not confirmed or not reviewer.strip():
        raise ValueError("실제 검수자가 --reviewer와 --confirm-reviewed로 검수 완료를 선언해야 함")
    package = Package.model_validate_json(file.read_text(encoding="utf-8"))
    # SAMPLE is deliberately rejected by authoring validation, even with a supplied reviewer.
    data = package.model_dump()
    data["review"] = {"reviewed_by": reviewer, "reviewed_at": today}
    reviewed = Package.model_validate(data)
    check_authoring(Path(f"{reviewed.code}.json"), reviewed, reviewed=True, today=today)
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / f"{reviewed.code}.json"
    if target.exists():
        raise ValueError("기존 검수본은 덮지 않음: 별도 새 검수 폴더 지정")
    preserve_artifact(target, reviewed.model_dump_json(by_alias=True, indent=2) + "\n")


def cache_summary(companies: list[CompanySeed], cache: Path) -> list[dict[str, object]]:
    result = []
    for company in companies:
        try:
            documents = cached_documents(company, cache)
            result.append(
                {
                    "company": company.code,
                    "documents": len(documents),
                    "manual": sum(d.acquisition == "manual" for d in documents),
                }
            )
        except (ValueError, OSError):
            result.append({"company": company.code, "documents": 0})
    return result
