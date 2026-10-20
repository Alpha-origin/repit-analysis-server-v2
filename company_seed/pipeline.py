"""CLI orchestration. It never writes reviewed data or calls the service DB."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from anthropic import APIError, APITimeoutError

from company_seed.collection import Collector, cached_documents
from company_seed.dart import DartClient, enrich
from company_seed.documents import CompanySeed
from company_seed.extraction import PROMPT_VERSION, ClaudeExtractor, Extraction, Extractor, extract_cached
from company_seed.rendering import BrowserRenderer
from company_seed.schema import Package, check_authoring
from company_seed.storage import preserve_artifact, write_json

ROOT = Path(__file__).parent


def review_note(company: CompanySeed, package: Package, extracted: Extraction) -> str:
    lines = [
        f"# {company.display_name} 자동 추출 검수",
        "",
        "**사람 검수 미완료. 서비스 반영 불가.**",
        "",
        f"법인: {package.legal_name}",
        f"범위: {company.boundary_note}",
        "",
        "근거 구절의 원문 포함 여부를 검사했다. 문장 의미의 정확성과 법인 소속은 사람이 확인해야 한다.",
        "원문 구절은 로컬 검수용이다. 원문 캐시·이 검수 파일을 그대로 공개 저장소에 올리지 않는다.",
        "",
    ]
    if package.dart_corp_code:
        lines += [f"DART 고유번호: {package.dart_corp_code}", f"사업자등록번호: {package.business_reg_no or '미확인'}"]
        lines += [
            f"식별 근거(키 제외 API 주소): {source.url}" for source in package.sources if source.source_type == "DART"
        ]
    for item in extracted.facts:
        source = next(s for s in package.sources if s.source_key == item.fact.source_key)
        lines.extend(
            [
                f"## {item.fact.kind}: {item.fact.title}",
                "",
                item.fact.content,
                f"- 출처: {source.url}",
                f"- 문단: {item.evidence.passage_id}",
                f"- 근거: {item.evidence.quote}",
                "- [ ] 사실과 범위 확인",
                "",
            ]
        )
    lines += [
        "- [ ] 법인명·서비스 운영 주체 확인",
        "- [ ] 파생 연습 신호 확인",
        "- [ ] 요약이 사실 범위 안인지 확인",
        "- [ ] 검수 완료 사본에 실제 검수자·날짜 기록",
        "",
    ]
    if any(source.source_type == "SAMPLE" for source in package.sources):
        lines.insert(2, "가상 SAMPLE 데모: 실제 기업 자료·실제 모델 출력이 아닙니다. 승인·실제 export 금지.")
    return "\n".join(lines)


def prepare_run(
    command: str,
    companies: list[CompanySeed],
    cache: Path,
    output: Path,
    collector: Collector,
    extractor: Extractor | None,
    *,
    resume: bool,
    demo: bool,
    dart_enabled: bool,
) -> list[dict[str, object]]:
    manifest = {
        "version": 3,
        "promptVersion": PROMPT_VERSION,
        "command": command,
        "companies": [company.model_dump(mode="json") for company in companies],
        "cache": str(cache.resolve()),
        "model": extractor.model if extractor else None,
        "modelTimeoutSeconds": extractor.timeout_seconds if isinstance(extractor, ClaudeExtractor) else None,
        "mode": "SAMPLE_DEMO" if demo else "LIVE",
        "render": collector.renderer is not None,
        "dartEnabled": dart_enabled,
    }
    if resume:
        previous = json.loads((output / "run.json").read_text(encoding="utf-8"))
        if previous != manifest:
            raise ValueError("재개 설정 불일치: 명령·기업·모델·출처·모드·캐시가 같은 실행만 재개 가능")
        report = output / "report.json"
        if report.exists():
            rows: list[dict[str, object]] = json.loads(report.read_text(encoding="utf-8"))
            return rows
        return []
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "run.json", manifest)
    return []


async def process_company(
    command: str,
    company: CompanySeed,
    cache: Path,
    output: Path,
    collector: Collector,
    extractor: Extractor | None,
    dart: DartClient | None,
    row: dict[str, object],
    *,
    demo: bool,
) -> None:
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    partial = False
    if command in {"collect", "run"}:
        try:
            async with asyncio.timeout(180):
                collected = await collector.collect(company, cache, today)
        except TimeoutError:
            collected = json.loads((cache / company.code / "latest.json").read_text(encoding="utf-8"))
            row["collectionTimeout"] = True
            partial = True
        row["collection"] = collected
        partial = partial or any(page["status"] == "failed" for page in collected["pages"])
        if command == "collect" and not collected["documents"]:
            row["status"] = "failed"
            return
    if command == "extract":
        cached_collection = json.loads((cache / company.code / "latest.json").read_text(encoding="utf-8"))
        row["collection"] = cached_collection
        partial = cached_collection.get("inProgress", False) or any(
            page["status"] == "failed" for page in cached_collection["pages"]
        )
    if command in {"extract", "run"}:
        if extractor is None:
            raise ValueError("추출 모델 설정 없음")
        identity = None
        if dart:
            try:
                identity, status = await dart.resolve(company, today)
                row["dart"] = status
            except (ValueError, OSError) as exc:
                row["dart"] = "unavailable: " + str(exc)
        else:
            row["dart"] = "not_configured"
        target = company.model_copy(update={"legal_name": identity.legal_name}) if identity else company
        documents = cached_documents(company, cache, versions=collected["documents"] if command == "run" else None)
        row["evidenceVersions"] = [
            {"sourceKey": doc.source.source_key, "hash": doc.content_hash, "acquisition": doc.acquisition}
            for doc in documents
        ]
        package, extracted, reused = await extract_cached(
            target,
            documents,
            cache,
            extractor,
            allow_samples=demo,
        )
        package = enrich(package, identity)
        check_authoring(Path(f"{company.code}.json"), package, reviewed=False, today=today, allow_samples=demo)
        preserve_artifact(
            output / "drafts" / f"{company.code}.json", package.model_dump_json(by_alias=True, indent=2) + "\n"
        )
        preserve_artifact(output / "review" / f"{company.code}.md", review_note(company, package, extracted))
        row.update(extraction="cached" if reused else "generated", factCount=len(package.facts))
    row["status"] = (
        "SAMPLE_DEMO" if demo else "partial" if partial else "collected" if command == "collect" else "draft"
    )


async def execute(
    command: str,
    companies: list[CompanySeed],
    cache: Path,
    output: Path,
    collector: Collector,
    extractor: Extractor | None,
    *,
    resume: bool = False,
    demo: bool = False,
    dart: DartClient | None = None,
) -> int:
    rows = await asyncio.to_thread(
        prepare_run,
        command,
        companies,
        cache,
        output,
        collector,
        extractor,
        resume=resume,
        demo=demo,
        dart_enabled=dart is not None,
    )
    previous = {row["company"]: row for row in rows}
    for company in companies:
        existing = previous.get(company.code)
        if existing and (
            existing["status"] in {"draft", "collected", "SAMPLE_DEMO"}
            or (existing["status"] == "partial" and (output / "drafts" / f"{company.code}.json").exists())
        ):
            sys.stdout.write(f"{company.code}: 기존 결과 유지\n")
            continue
        row: dict[str, object] = {"company": company.code, "mode": "SAMPLE_DEMO" if demo else "LIVE"}
        started = time.monotonic()
        calls_before = extractor.calls if isinstance(extractor, ClaudeExtractor) else 0
        try:
            await process_company(command, company, cache, output, collector, extractor, dart, row, demo=demo)
        except (ValueError, OSError, APIError) as exc:
            row.update(status="failed", error=type(exc).__name__ if isinstance(exc, APIError) else str(exc))
            if isinstance(exc, APITimeoutError) and isinstance(extractor, ClaudeExtractor):
                row.update(
                    errorCode="APITimeoutError",
                    error=f"APITimeoutError: 모델 요청 타임아웃({extractor.timeout_seconds:g}초 설정). "
                    "서버 처리·과금 여부 미확인, 자동 재시도 없음. "
                    "--model-timeout-seconds 조정 후 새 --out으로 실행 가능",
                )
        if isinstance(extractor, ClaudeExtractor):
            row["modelRequestsStarted"] = extractor.calls - calls_before
            if extractor.calls > calls_before:
                row["usage"] = extractor.usage
        else:
            row["modelRequestsStarted"] = 0
        row["elapsedSeconds"] = round(time.monotonic() - started, 3)
        rows = [item for item in rows if item["company"] != company.code]
        rows.append(row)
        sys.stdout.write(f"{company.code}: {row['status']}\n")
        write_json(output / "report.json", rows)
        if row["status"] == "failed":
            sys.stderr.write(f"  원인: {row.get('error', '수집 가능한 본문 없음')}\n")
        if row["status"] in {"failed", "partial"}:
            sys.stderr.write(f"  상세 보고서: {output / 'report.json'}\n")
    return int(any(row["status"] in {"failed", "partial"} for row in rows))


def main(argv: list[str]) -> int:
    from company_seed.pipeline_cli import main as cli_main  # noqa: PLC0415 -- CLI/orchestrator cycle

    return cli_main(argv)


async def run_http(
    command: str,
    companies: list[CompanySeed],
    cache: Path,
    output: Path,
    extractor: Extractor | None,
    *,
    render: bool = True,
    resume: bool = False,
    use_dart: bool = True,
) -> int:
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(15, connect=5), follow_redirects=False, trust_env=False
    ) as client:
        key = os.environ.get("DART_API_KEY") if use_dart else None
        dart = DartClient(client, key, cache / "dart") if key else None
        collector = Collector(client, renderer=BrowserRenderer() if render else None)
        return await execute(command, companies, cache, output, collector, extractor, resume=resume, dart=dart)
