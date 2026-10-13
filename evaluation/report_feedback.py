"""반복 채점 JSONL과 사람 평가 CSV에서 검증 결과표를 만든다."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

AXES = ("intent", "depth", "specificity", "accuracy")
QUALITY_AXES = ("question_fit", "groundedness", "technical_correctness", "actionability")


def load_runs(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise ValueError("반복 채점 결과가 비어 있습니다")
    keys = [(row["case_id"], row["variant"], row["repeat"]) for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("case_id/variant/repeat 조합이 중복됐습니다")
    return rows


def load_csv(path: Path | None, required: set[str]) -> list[dict[str, str]]:
    if path is None:
        return []
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError(f"CSV 필수 열이 없습니다: {', '.join(sorted(required))}")
        return list(reader)


def p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(len(ordered) * 0.95) - 1]


def grade_agreement(rows: list[dict[str, Any]]) -> float | None:
    """문항·축마다 반복 실행에서 가장 빈번한 등급의 비율을 평균한다."""
    grouped: dict[tuple[str, str], list[int]] = defaultdict(list)
    for row in rows:
        for question_id, levels in row["question_levels"].items():
            for axis in AXES:
                value = levels[axis]
                if value is not None:
                    grouped[(question_id, axis)].append(value)
    comparable = [values for values in grouped.values() if len(values) >= 2]
    if not comparable:
        return None
    return statistics.mean(Counter(values).most_common(1)[0][1] / len(values) for values in comparable)


def human_comparison(runs: list[dict[str, Any]], human: list[dict[str, str]]) -> list[str]:
    if not human:
        return ["사람 평가: 미입력 — 전문 면접관과의 일치도를 주장할 수 없음"]
    by_case: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in human:
        by_case[(row["case_id"], row["question_id"])].append(row)
    differences: list[int] = []
    reviewers = {row["reviewer_id"] for row in human}
    for run in runs:
        if run["status"] != "succeeded":
            continue
        for question_id, levels in run["question_levels"].items():
            for rating in by_case.get((run["case_id"], question_id), []):
                for axis in AXES:
                    if levels[axis] is None or rating[axis] == "":
                        continue
                    human_level = int(rating[axis])
                    if not 0 <= human_level <= 4:
                        raise ValueError(f"사람 평가 등급 범위 오류: {rating['case_id']}/{axis}")
                    differences.append(abs(levels[axis] - human_level))
    if not differences:
        return ["사람 평가: 입력은 있지만 성공한 실행과 일치하는 문항·축이 없음"]
    exact = sum(delta == 0 for delta in differences) / len(differences)
    within_one = sum(delta <= 1 for delta in differences) / len(differences)
    return [
        f"사람 평가자 {len(reviewers)}명, 축 비교 {len(differences)}건",
        f"등급 정확 일치 {exact:.1%}, ±1등급 이내 {within_one:.1%}, 평균 절대 차이 {statistics.mean(differences):.2f}",
        "동일 문항의 반복 실행과 평가자 수가 비교 건수에 중복 반영됨",
    ]


def quality_comparison(quality: list[dict[str, str]], runs: list[dict[str, Any]]) -> list[str]:
    if not quality:
        return ["산출물 내용 평가: 미입력 — 프롬프트 품질 향상을 주장할 수 없음"]
    successful = {
        (row["case_id"], row["variant"], str(row["repeat"]))
        for row in runs if row["status"] == "succeeded"
    }
    reviewed: set[tuple[str, str, str, str]] = set()
    values: dict[tuple[str, str], list[int]] = defaultdict(list)
    for row in quality:
        key = (row["case_id"], row["variant"], row["repeat"])
        reviewer_key = (*key, row["reviewer_id"])
        if key not in successful or reviewer_key in reviewed:
            raise ValueError(f"품질 평가는 성공한 실행에 한 번씩만 연결해야 합니다: {key}")
        reviewed.add(reviewer_key)
        for axis in QUALITY_AXES:
            if row[axis] == "":
                continue
            score = int(row[axis])
            if not 0 <= score <= 4:
                raise ValueError(f"품질 평가 범위 오류: {row['case_id']}/{axis}")
            values[(row["variant"], axis)].append(score)
    lines = ["| 프롬프트 | 기준 | 평균(0~4) | 평가 수 |", "|---|---|---:|---:|"]
    for (variant, axis), scores in sorted(values.items()):
        lines.append(f"| {variant} | {axis} | {statistics.mean(scores):.2f} | {len(scores)} |")
    return lines


def report(runs: list[dict[str, Any]], human: list[dict[str, str]], quality: list[dict[str, str]]) -> str:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in runs:
        grouped[(row["case_id"], row["variant"])].append(row)
    lines = [
        "# 피드백 검증 결과", "",
        f"생성 시각: {datetime.now(UTC).isoformat()}",
        "", "| 사례 | 프롬프트 | 성공/시도 | 총점 범위 | 표준편차 | 축 등급 반복 일치 | 호출 p95(초) |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for (case_id, variant), rows in sorted(grouped.items()):
        ok = [row for row in rows if row["status"] == "succeeded"]
        scores = [row["total_score"] for row in ok]
        span = f"{min(scores)}~{max(scores)}" if scores else "—"
        deviation = f"{statistics.pstdev(scores):.2f}" if len(scores) >= 2 else "—"
        agreement = grade_agreement(ok)
        agreement_text = f"{agreement:.1%}" if agreement is not None else "—"
        duration = p95([row["duration_seconds"] for row in rows])
        duration_text = f"{duration:.2f}" if duration is not None else "—"
        lines.append(
            f"| {case_id} | {variant} | {len(ok)}/{len(rows)} | {span} | {deviation} | "
            f"{agreement_text} | {duration_text} |"
        )
    lines.extend(["", "## 사람 평가와 AI 등급", "", *human_comparison(runs, human)])
    lines.extend(["", "## 피드백 내용 품질", "", *quality_comparison(quality, runs)])
    lines.extend([
        "", "## 해석 주의", "",
        "- 점수 계산식의 결정성과 LLM 축 등급의 반복 안정성은 서로 다르다.",
        "- 위 호출 시간은 순차적 LLM 평가 시간이며 동시 이용 부하 테스트 결과가 아니다.",
        "- 사람 평가의 근거와 대표적인 일치·불일치 출력은 별도로 첨부해야 한다.",
        "- 프롬프트 간 모델·입력·도구 스키마가 같았는지 prompt_sha256과 input_sha256을 확인한다.",
    ])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--human", type=Path)
    parser.add_argument("--quality", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    human = load_csv(args.human, {"case_id", "question_id", "reviewer_id", *AXES})
    quality = load_csv(args.quality, {"case_id", "variant", "repeat", "reviewer_id", *QUALITY_AXES})
    content = report(load_runs(args.runs), human, quality)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8")
    print(f"보고서 저장: {args.output}")


if __name__ == "__main__":
    main()
