"""버전별 비교와 competency-v1 전환 판단용 보고서."""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any

from evaluation.competency_gate import transition_checks
from evaluation.competency_metrics import (
    AXES,
    inter_rater_pairs,
    rating_pairs,
    session_correlations,
    stability,
    weighted_kappa,
)


def display(value: float | None, *, percent: bool = False) -> str:
    if value is None:
        return "미측정"
    return f"{value:.1%}" if percent else f"{value:.3f}"


def render_report(runs: list[dict[str, Any]], human: list[dict[str, str]]) -> str:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in runs:
        grouped[(row.get("scoring_version", "axis-v4"), row["variant"])].append(row)
    lines = [
        "# 역량 채점 비교 보고서",
        "",
        "판정 원칙: 미측정·미충족 기준이 있으면 운영 전환 보류 아래 버전별 초기 기준표 확인",
        "",
    ]
    for (version, variant), rows in sorted(grouped.items()):
        lines.extend(
            [
                "",
                f"## {version} / {variant}",
                "",
                "| 버전 | 프롬프트 | 성공/시도 | 실패율 | 반복 최빈 일치 | 총점 표준편차 평균 | "
                "입력/출력 토큰 평균 | 시간 평균(초) |",
                "|---|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        ok = [row for row in rows if row["status"] == "succeeded"]
        agreement, deviation = stability(ok)
        tokens = [
            f"{statistics.mean([r[key] for r in rows]):.0f}" if all(key in r for r in rows) else "미측정"
            for key in ("input_tokens", "output_tokens")
        ]
        duration = statistics.mean([r["duration_seconds"] for r in rows])
        lines.append(
            f"| {version} | {variant} | {len(ok)}/{len(rows)} | {1 - len(ok) / len(rows):.1%} | "
            f"{display(agreement, percent=True)} | {display(deviation)} | {' / '.join(tokens)} | {duration:.2f} |"
        )
        truncated = (
            str(sum(r["stop_reason"] == "max_tokens" for r in rows))
            if all("stop_reason" in r for r in rows)
            else "미측정"
        )
        lines.extend(["", f"{version}/{variant}: 토큰 제한 종료 {truncated}/{len(rows)}건", ""])
        if version != "competency-v1":
            continue
        baseline = grouped.get(("axis-v4", variant), [])
        gates = transition_checks(rows, baseline, human)
        lines.extend(["| 전환 기준 초기값 | 결과 |", "|---|---|"])
        lines.extend(f"| {name} | {status} |" for name, status in gates)
        if all(status == "통과" for _, status in gates):
            lines.append("모든 자동 기준 충족 운영 전환은 API·웹 배포 및 사람 검토 후 별도 결정")
        lines.extend(_human_lines(ok, human))
        lines.extend(_mapping_lines(ok, human))
        lines.extend(_stability_lines(ok))
        correlations = session_correlations(ok)
        lines.extend(["", "역량 점수 상관 행렬: 사례별 반복 평균 Pearson r 동일 사례에서 두 역량이 관측된 경우만", ""])
        axes = AXES[:-1]
        lines.extend(["| 역량 | " + " | ".join(axes) + " |", "|---|" + "---:|" * len(axes)])
        for a in axes:
            cells = ["1" if a == b else display(correlations.get((a, b), correlations.get((b, a)))) for b in axes]
            lines.append(f"| {a} | " + " | ".join(cells) + " |")
        lines.extend(_expectation_lines(rows))
    reviewers = len({r["reviewer_id"] for r in human})
    lines.extend(
        [
            "",
            f"사람 평가자 {reviewers}명 두 명 미만이거나 지표가 미측정이면 전환 근거가 충족되지 않음",
            "합격 기준 숫자는 팀의 초기값이며 자동으로 운영 설정을 전환하지 않음",
            "합성 expected는 설계 기대값이며 실제 사람 평가와 다름",
            "실패율은 운영과 같은 최대 2회 제출 정책 적용 후 기준 토큰과 시간은 재시도를 포함",
            "사람 평가는 조정 전과 후 CSV를 각각 보존하고 별도 보고서를 생성할 것",
            "운영 2주 관찰과 실제 합불 수집은 이 보고서로 완료되지 않음",
            "",
        ]
    )
    return "\n".join(lines)


def _human_lines(runs: list[dict[str, Any]], human: list[dict[str, str]]) -> list[str]:
    lines = [
        "| 역량 | 비교 수 | 모델-사람 카파 | 정확 일치 | ±1 일치 | 사람 간 카파 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for axis in ("response", *AXES):
        pairs = rating_pairs(runs, human, axis)
        exact = sum(a == b for a, b in pairs) / len(pairs) if pairs else None
        nearby = sum(abs(a - b) <= 1 for a, b in pairs) / len(pairs) if pairs else None
        kappa = weighted_kappa(pairs)
        inter = weighted_kappa(inter_rater_pairs(human, axis))
        lines.append(
            f"| {axis} | {len(pairs)} | {display(kappa)} | {display(exact, percent=True)} | "
            f"{display(nearby, percent=True)} | {display(inter)} |"
        )
    primary_pairs = [pair for axis in AXES for pair in rating_pairs(runs, human, axis, primary_only=True)]
    inter_pairs = [pair for axis in AXES for pair in inter_rater_pairs(human, axis, primary_only=True)]
    lines.extend(
        [
            "",
            f"주 역량 모델-사람 이차 가중 카파: {display(weighted_kappa(primary_pairs))}",
            f"주 역량 사람 간 이차 가중 카파: {display(weighted_kappa(inter_pairs))}",
        ]
    )
    return lines


def _mapping_lines(runs: list[dict[str, Any]], human: list[dict[str, str]]) -> list[str]:
    primary = {(r["case_id"], qid): target for r in runs for qid, target in r.get("targets", {}).items()}
    pairs = [(primary.get((h["case_id"], h["question_id"])), h["primary"].upper()) for h in human if h.get("primary")]
    pairs = [
        (target, expected)
        for target, expected in pairs
        if target is not None and target["source"] in {"job", "non_tech"}
    ]
    agreement = sum(target["primary"] == expected for target, expected in pairs) / len(pairs) if pairs else None
    return ["", f"카테고리 매핑과 평가자 주 역량 일치율: {display(agreement, percent=True)} ({len(pairs)}건)"]


def _stability_lines(runs: list[dict[str, Any]]) -> list[str]:
    lines = ["", "| 역량 | 반복 최빈 일치 |", "|---|---:|"]
    for axis in ("response", *AXES):
        subset = [
            {
                **r,
                "question_levels": {
                    qid: {axis: levels.get(axis.upper(), levels.get(axis))}
                    for qid, levels in r["question_levels"].items()
                },
            }
            for r in runs
        ]
        agreement, _ = stability(subset)
        lines.append(f"| {axis} | {display(agreement, percent=True)} |")
    return lines


def _expectation_lines(runs: list[dict[str, Any]]) -> list[str]:
    grouped: dict[str, list[bool]] = defaultdict(list)
    for row in runs:
        checks = row.get("expectation_checks", {})
        if checks:
            for tag in row.get("tags", []):
                grouped[tag].append(row["status"] == "succeeded" and all(checks.values()))
    lines = ["", "| 태그별 설계 기대값 | 통과/검증 실행 |", "|---|---:|"]
    lines.extend(f"| {tag} | {sum(values)}/{len(values)} |" for tag, values in sorted(grouped.items()))
    if not grouped:
        lines.append("| 기대값 검증 미측정 | — |")
    return lines
