"""User-facing pipeline commands. Only extract/run can spend model tokens."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from company_seed.demo import run_demo
from company_seed.documents import CompanySeed, read_registry
from company_seed.extraction import DEFAULT_MODEL_TIMEOUT_SECONDS, ClaudeExtractor
from company_seed.operations import approve, cache_summary, doctor, import_evidence
from company_seed.pipeline import run_http
from company_seed.reporting import summarize

ROOT = Path(__file__).parent


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="기업 데이터 수집·근거 추출·실행 복구·검수 도구")
    result.add_argument(
        "command",
        choices=("plan", "doctor", "status", "collect", "extract", "run", "import", "approve", "demo", "summary"),
    )
    group = result.add_mutually_exclusive_group()
    group.add_argument("--company", action="append")
    group.add_argument("--all", action="store_true")
    result.add_argument("--registry", type=Path, default=ROOT / "sources.json")
    result.add_argument("--cache", type=Path, default=ROOT / ".cache/pipeline")
    result.add_argument("--out", type=Path)
    result.add_argument("--model", default=os.environ.get("COMPANY_SEED_MODEL"))
    result.add_argument("--max-model-calls", type=int, default=1)
    result.add_argument("--model-timeout-seconds", type=float, default=DEFAULT_MODEL_TIMEOUT_SECONDS)
    result.add_argument("--render", choices=("auto", "never"), default="auto")
    result.add_argument("--resume", action="store_true")
    result.add_argument("--no-dart", action="store_true")
    result.add_argument("--file", type=Path)
    result.add_argument("--url")
    result.add_argument("--scope")
    result.add_argument("--reviewer")
    result.add_argument("--confirm-reviewed", action="store_true")
    return result


def select(args: argparse.Namespace) -> list[CompanySeed]:
    companies = read_registry(args.registry)
    if args.company:
        unknown = set(args.company) - {c.code for c in companies}
        if unknown:
            raise ValueError(f"등록되지 않은 기업: {', '.join(sorted(unknown))}")
        return [company for company in companies if company.code in args.company]
    if args.all:
        return companies
    raise ValueError("--company 또는 --all로 대상 지정 필요")


def local_command(args: argparse.Namespace, companies: list[CompanySeed]) -> int | None:
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    if args.command == "plan":
        for company in companies:
            sys.stdout.write(f"{company.code}\t{company.display_name}\t출처 {len(company.sources)}개\n")
        sys.stdout.write(f"대상 {len(companies)}개. 기업당 최대 12페이지. 네트워크·모델 호출 없음.\n")
    elif args.command == "doctor":
        sys.stdout.write(json.dumps(doctor(companies, args.model), ensure_ascii=False, indent=2) + "\n")
    elif args.command == "status":
        sys.stdout.write(json.dumps(cache_summary(companies, args.cache), ensure_ascii=False, indent=2) + "\n")
    elif args.command == "import":
        if len(companies) != 1 or not args.file or not args.url or not args.scope:
            raise ValueError("import는 기업 하나와 --file·--url·--scope 필요")
        import_evidence(companies[0], args.cache, args.file, args.url, args.scope, today)
        sys.stdout.write("수동 반입 완료. acquisition=manual, 사람 검수 필요.\n")
    else:
        return None
    return 0


def main(argv: list[str]) -> int:
    args = parser().parse_args(argv)
    try:
        standalone = standalone_command(args)
        if standalone is not None:
            return standalone
        companies = select(args)
        completed = local_command(args, companies)
        if completed is not None:
            return completed
        if args.out is None:
            raise ValueError("--out에 새 실행 결과 폴더 지정 필요")
        if args.command in {"extract", "run"} and not args.model:
            raise ValueError("--model 또는 COMPANY_SEED_MODEL에 사용할 모델 ID 지정 필요")
        if args.max_model_calls < 1:
            raise ValueError("--max-model-calls는 1 이상 필요")
        extractor = (
            ClaudeExtractor(args.model, max_calls=args.max_model_calls, timeout_seconds=args.model_timeout_seconds)
            if args.model
            else None
        )
        return asyncio.run(
            run_http(
                args.command,
                companies,
                args.cache,
                args.out,
                extractor,
                render=args.render == "auto",
                resume=args.resume,
                use_dart=not args.no_dart,
            )
        )
    except (ValueError, OSError) as exc:
        sys.stderr.write(f"실행 실패: {exc}\n")
        return 1


def standalone_command(args: argparse.Namespace) -> int | None:
    if args.command == "summary":
        if not args.file:
            raise ValueError("summary는 --file report.json 필요")
        sys.stdout.write(json.dumps(summarize(args.file), ensure_ascii=False, indent=2) + "\n")
        return 0
    if args.command == "demo":
        if not args.out:
            raise ValueError("demo는 --out에 새 결과 폴더 필요")
        return asyncio.run(run_demo(args.out, resume=args.resume))
    if args.command == "approve":
        if not args.file or not args.out or not args.reviewer:
            raise ValueError("approve는 --file·--out·--reviewer 필요")
        approve(
            args.file,
            args.out,
            args.reviewer,
            confirmed=args.confirm_reviewed,
            today=datetime.now(ZoneInfo("Asia/Seoul")).date(),
        )
        sys.stdout.write("사용자 검수 선언 기록 완료. 묶음 전체 validate/export를 별도로 실행하세요.\n")
        return 0
    return None
