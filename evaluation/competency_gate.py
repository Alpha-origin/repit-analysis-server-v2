"""05 검증 명세의 초기 전환 기준 비교 미측정은 통과로 처리하지 않는다."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from evaluation.competency_metrics import (
    AXES,
    inter_rater_pairs,
    rating_pairs,
    session_correlations,
    stability,
    weighted_kappa,
)

MAX_FAILURE_RATE = 0.02
MIN_KAPPA = 0.6
MIN_REVIEWERS = 2
MIN_MAPPING_AGREEMENT = 0.8
MAX_CORRELATION = 0.8
MIN_REPEATS = 5


def transition_checks(
    rows: list[dict[str, Any]],
    baseline: list[dict[str, Any]],
    human: list[dict[str, str]],
) -> list[tuple[str, str]]:
    ok = [r for r in rows if r["status"] == "succeeded"]
    base_ok = [r for r in baseline if r["status"] == "succeeded"]
    result = []

    def check(name: str, passed: bool | None) -> None:
        result.append((name, "미측정" if passed is None else "통과" if passed else "미충족"))

    failure = 1 - len(ok) / len(rows) if rows else None
    base_failure = 1 - len(base_ok) / len(baseline) if baseline else None
    check(
        "재시도 후 실패율 ≤ 기존 및 2%",
        failure <= base_failure and failure <= MAX_FAILURE_RATE
        if failure is not None and base_failure is not None
        else None,
    )
    primary = [pair for axis in AXES for pair in rating_pairs(ok, human, axis, primary_only=True)]
    inter = [pair for axis in AXES for pair in inter_rater_pairs(human, axis, primary_only=True)]
    for name, pairs in (("주 역량 모델-사람 카파 ≥ 0.6", primary), ("주 역량 사람 간 카파 ≥ 0.6", inter)):
        value = weighted_kappa(pairs)
        check(name, value >= MIN_KAPPA if value is not None else None)
    check("사람 평가자 2명 이상", len({h["reviewer_id"] for h in human}) >= MIN_REVIEWERS)
    targets = {(r["case_id"], qid): target for r in ok for qid, target in r.get("targets", {}).items()}
    mapping = [(targets.get((h["case_id"], h["question_id"])), h["primary"].upper()) for h in human if h.get("primary")]
    mapping = [(target, expected) for target, expected in mapping if target and target["source"] in {"job", "non_tech"}]
    check(
        "주 역량 매핑 일치 ≥ 80%",
        sum(t["primary"] == expected for t, expected in mapping) / len(mapping) >= MIN_MAPPING_AGREEMENT
        if mapping
        else None,
    )
    v1_agreement, v1_deviation = stability(_only_axes(ok, {a.upper() for a in AXES[:-1]}))
    v4_agreement, v4_deviation = stability(_only_axes(base_ok, {"depth"}))
    check(
        "역량 반복 최빈 일치 ≥ 기존 깊이",
        v1_agreement >= v4_agreement if v1_agreement is not None and v4_agreement is not None else None,
    )
    check(
        "종합 점수 표준편차 ≤ 기존",
        v1_deviation <= v4_deviation if v1_deviation is not None and v4_deviation is not None else None,
    )
    correlations = list(session_correlations(ok).values())
    check(
        "모든 역량 쌍 상관 < 0.8",
        all(abs(value) < MAX_CORRELATION for value in correlations if value is not None)
        if correlations and all(value is not None for value in correlations)
        else None,
    )
    check(
        "출력 토큰 제한 종료 0건",
        all(r["stop_reason"] != "max_tokens" for r in rows) if rows and all("stop_reason" in r for r in rows) else None,
    )
    checks_by_tag: dict[str, list[bool]] = defaultdict(list)
    for r in rows:
        for tag in r.get("tags", []):
            if r.get("expectation_checks"):
                checks_by_tag[tag].append(r["status"] == "succeeded" and all(r["expectation_checks"].values()))
    tags = {
        "implementation-no-reason",
        "team-only",
        "subject-omitted",
        "honest-unknown",
        "confident-wrong",
        "collaboration",
        "growth",
        "motivation",
        "follow-contradiction",
        "follow-weak",
        "legacy-category",
        "general-job",
    }
    check(
        "필수 태그별 기대 동작 전부 통과",
        all(all(checks_by_tag[tag]) for tag in tags) if tags <= checks_by_tag.keys() else None,
    )
    repeats = defaultdict(set)
    for r in rows:
        repeats[r["case_id"]].add(r["repeat"])
    check("사례별 반복 5회 이상", all(len(values) >= MIN_REPEATS for values in repeats.values()) if repeats else None)
    return result


def _only_axes(runs: list[dict[str, Any]], axes: set[str]) -> list[dict[str, Any]]:
    return [
        {
            **r,
            "question_levels": {
                qid: {axis: grade for axis, grade in levels.items() if axis in axes}
                for qid, levels in r["question_levels"].items()
            },
        }
        for r in runs
    ]
