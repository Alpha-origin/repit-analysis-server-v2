"""CLI orchestration. It never writes reviewed data or calls the service DB."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from anthropic import APIError

from company_seed.collection import Collector, cached_documents
from company_seed.documents import CompanySeed, read_registry
from company_seed.extraction import ClaudeExtractor, Extraction, Extractor, extract_cached
from company_seed.schema import Package, check_authoring

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
    return "\n".join(lines)


async def execute(
    command: str,
    companies: list[CompanySeed],
    cache: Path,
    output: Path,
    collector: Collector,
    extractor: Extractor | None,
) -> int:
    await asyncio.to_thread(output.mkdir, parents=True, exist_ok=False)
    rows: list[dict[str, object]] = []
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    for company in companies:
        row: dict[str, object] = {"company": company.code}
        try:
            collection_failed = False
            if command in {"collect", "run"}:
                collected = await collector.collect(company, cache, today)
                row["collection"] = collected
                collection_failed = any(p["status"] == "failed" for p in collected["pages"])
            if command in {"extract", "run"}:
                if extractor is None:
                    raise ValueError("추출 모델 설정 없음")
                package, extracted, reused = await extract_cached(
                    company,
                    cached_documents(company, cache),
                    cache,
                    extractor,
                )
                check_authoring(Path(f"{company.code}.json"), package, reviewed=False, today=today)
                for sub in ("drafts", "review"):
                    (output / sub).mkdir(exist_ok=True)
                (output / "drafts" / f"{company.code}.json").write_text(
                    package.model_dump_json(by_alias=True, indent=2) + "\n",
                    encoding="utf-8",
                )
                (output / "review" / f"{company.code}.md").write_text(
                    review_note(company, package, extracted),
                    encoding="utf-8",
                )
                row["extraction"] = "cached" if reused else "generated"
                row["factCount"] = len(package.facts)
                if isinstance(extractor, ClaudeExtractor) and not reused:
                    row["usage"] = extractor.usage
            if command == "collect" and not collected["documents"]:
                row["status"] = "failed"
            elif collection_failed:
                row["status"] = "partial"
            else:
                row["status"] = "draft" if command != "collect" else "collected"
        except (ValueError, OSError, APIError) as exc:
            row["status"] = "failed"
            # SDK errors may embed provider payloads. Do not print credentials or raw provider messages.
            row["error"] = type(exc).__name__ if isinstance(exc, APIError) else str(exc)
        rows.append(row)
        sys.stdout.write(f"{company.code}: {row['status']}\n")
        (output / "report.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return int(any(row["status"] in {"failed", "partial"} for row in rows))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="기업 데이터 수집→근거 추출→미검수 초안 생성")
    parser.add_argument("command", choices=("plan", "collect", "extract", "run"))
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--company", action="append")
    group.add_argument("--all", action="store_true")
    parser.add_argument("--registry", type=Path, default=ROOT / "sources.json")
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache/pipeline")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--model", default=os.environ.get("COMPANY_SEED_MODEL"))
    args = parser.parse_args(argv)
    try:
        companies = read_registry(args.registry)
        if args.company:
            unknown = set(args.company) - {c.code for c in companies}
            if unknown:
                raise ValueError(f"등록되지 않은 기업: {', '.join(sorted(unknown))}")
            companies = [c for c in companies if c.code in args.company]
        if args.command == "plan":
            for company in companies:
                sys.stdout.write(f"{company.code}\t{company.display_name}\t출처 {len(company.sources)}개\n")
            sys.stdout.write(f"대상 {len(companies)}개. 기업당 최대 12페이지. 네트워크·모델 호출 없음.\n")
            return 0
        if args.out is None:
            raise ValueError("--out에 새 실행 결과 폴더 지정 필요")
        if args.command in {"extract", "run"} and not args.model:
            raise ValueError("--model 또는 COMPANY_SEED_MODEL에 사용할 모델 ID 지정 필요")
        extractor = ClaudeExtractor(args.model) if args.model else None
        return asyncio.run(run_http(args.command, companies, args.cache, args.out, extractor))
    except (ValueError, OSError) as exc:
        sys.stderr.write(f"실행 실패: {exc}\n")
        return 1


async def run_http(
    command: str,
    companies: list[CompanySeed],
    cache: Path,
    output: Path,
    extractor: Extractor | None,
) -> int:
    async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as client:
        return await execute(command, companies, cache, output, Collector(client), extractor)
