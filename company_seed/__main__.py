from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from company_seed.pipeline import main as pipeline_main
from company_seed.schema import export_bundle, validate_bundle

ROOT = Path(__file__).parent


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "pipeline":
        return pipeline_main(sys.argv[2:])
    parser = argparse.ArgumentParser(description="기업 데이터 패키지의 초안·사람 검수본 검증")
    parser.add_argument("command", choices=("validate", "inventory", "export"))
    parser.add_argument("--stage", choices=("draft", "reviewed"), default="reviewed")
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    directory = args.directory or ROOT / "data" / ("drafts" if args.stage == "draft" else "reviewed")
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    try:
        if args.command == "export":
            if args.out is None or args.stage != "reviewed":
                raise ValueError("export는 --stage reviewed와 새 --out 폴더가 필요합니다.")
            export_bundle(directory, args.out, today=today)
            sys.stdout.write(f"검수본을 새 폴더에 내보냈습니다: {args.out}\n")
        elif args.command == "validate":
            packages = validate_bundle(directory, reviewed=args.stage == "reviewed", today=today)
            sys.stdout.write(
                f"{args.stage}: {len(packages)}개 형식 검사 통과. 원문 사실·사람 검수 여부는 별도입니다.\n"
            )
        else:
            with (ROOT / "companies.csv").open(encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))
            for row in rows:
                state = "초안 있음" if (ROOT / "data/drafts" / f"{row['code']}.json").is_file() else "초안 없음"
                sys.stdout.write(f"{row['code']}\t{row['display_name']}\t{state}\n")
            sys.stdout.write(f"대상 {len(rows)}개\n")
    except (ValueError, OSError) as exc:
        sys.stderr.write(f"검증 실패: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
