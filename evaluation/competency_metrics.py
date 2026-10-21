"""빈 표본을 성공으로 간주하지 않는 오프라인 채점 비교 지표."""

from __future__ import annotations

import itertools
import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Any

MIN_OBSERVATIONS = 2
MAX_LEVEL = 4

AXES = ("knowledge", "problem_solving", "ownership", "collaboration", "growth", "motivation")


def weighted_kappa(pairs: Sequence[tuple[int, int]]) -> float | None:
    """0~4 순서형 등급의 이차 가중 카파 한 등급만 관측되면 산출 불가."""
    if not pairs:
        return None
    left, right = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    observed = sum((a - b) ** 2 for a, b in pairs) / len(pairs)
    expected = sum(left[a] * right[b] * (a - b) ** 2 for a in left for b in right) / len(pairs) ** 2
    return 1 - observed / expected if expected else None


def correlation(pairs: Sequence[tuple[float, float]]) -> float | None:
    if len(pairs) < MIN_OBSERVATIONS:
        return None
    xs, ys = zip(*pairs, strict=True)
    mx, my = statistics.mean(xs), statistics.mean(ys)
    numerator = sum((x - mx) * (y - my) for x, y in pairs)
    denominator = math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))
    return numerator / denominator if denominator else None


def rating_pairs(
    runs: list[dict[str, Any]],
    human: list[dict[str, str]],
    axis: str,
    *,
    primary_only: bool = False,
) -> list[tuple[int, int]]:
    # 반복 실행의 최빈 등급과 각 평가자를 비교하여 반복 수가 표본 수를 부풀리지 않게 한다.
    grouped: dict[tuple[str, str], list[int]] = defaultdict(list)
    for run in runs:
        for qid, levels in run.get("question_levels", {}).items():
            value = levels.get(axis.upper(), levels.get(axis))
            if value is not None:
                grouped[(run["case_id"], qid)].append(value)
    pairs = []
    for rating in human:
        if primary_only and rating.get("primary", "").lower() != axis:
            continue
        value = rating.get(axis, "")
        grades = grouped.get((rating["case_id"], rating["question_id"]), [])
        if value and grades:
            grade = int(value)
            if not 0 <= grade <= MAX_LEVEL:
                raise ValueError("사람 평가 등급은 0~4여야 합니다")
            pairs.append((Counter(grades).most_common(1)[0][0], grade))
    return pairs


def inter_rater_pairs(human: list[dict[str, str]], axis: str, *, primary_only: bool = False) -> list[tuple[int, int]]:
    grouped: dict[tuple[str, str], list[int]] = defaultdict(list)
    seen = set()
    for rating in human:
        key = (rating["case_id"], rating["question_id"], rating["reviewer_id"])
        if key in seen:
            raise ValueError("같은 문항의 평가자 등급이 중복됐습니다")
        seen.add(key)
        value = rating.get(axis, "")
        if value and (not primary_only or rating.get("primary", "").lower() == axis):
            grade = int(value)
            if not 0 <= grade <= MAX_LEVEL:
                raise ValueError("사람 평가 등급은 0~4여야 합니다")
            grouped[key[:2]].append(grade)
    return [pair for grades in grouped.values() for pair in itertools.combinations(grades, 2)]


def stability(runs: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    levels: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    totals: dict[str, list[float]] = defaultdict(list)
    for run in runs:
        totals[run["case_id"]].append(run["total_score"])
        for qid, axes in run["question_levels"].items():
            for axis, value in axes.items():
                if value is not None:
                    levels[(run["case_id"], qid, axis)].append(value)
    repeated = [values for values in levels.values() if len(values) >= MIN_OBSERVATIONS]
    deviations = [statistics.pstdev(values) for values in totals.values() if len(values) >= MIN_OBSERVATIONS]
    agreement = (
        statistics.mean(Counter(values).most_common(1)[0][1] / len(values) for values in repeated) if repeated else None
    )
    return agreement, statistics.mean(deviations) if deviations else None


def session_correlations(runs: list[dict[str, Any]]) -> dict[tuple[str, str], float | None]:
    # 사례별 반복 평균을 한 세션으로 보며 관측된 두 역량만 짝짓는다.
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    for run in runs:
        for axis, score in run["axis_scores"].items():
            if score is not None:
                grouped[(run["case_id"], axis.lower())].append(score)
    means = {key: statistics.mean(values) for key, values in grouped.items()}
    cases = {case for case, _ in means}
    return {
        (a, b): correlation(
            [(means[(case, a)], means[(case, b)]) for case in cases if (case, a) in means and (case, b) in means]
        )
        for a, b in itertools.combinations(AXES[:-1], 2)
    }
