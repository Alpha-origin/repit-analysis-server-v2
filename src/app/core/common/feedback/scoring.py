from __future__ import annotations

import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 채점 방식이 바뀌면 올린다. 적용 전후로 저장된 totalScore 의 의미가 달라서
# 로그로 어느 방식의 점수인지 구분할 수 있어야 한다.
SCORING_VERSION = "axis-v1"

# LLM 은 축마다 0~4 등급만 매기고, 점수는 서버가 아래 공식으로 계산한다.
# 등급 기준 문구는 각 피드백 프롬프트의 [축별 등급 기준] 에 있다.
LEVEL_POINTS = 25
MAX_LEVEL = 4

# 축 가중치(%). 의도 충족은 나머지 축의 전제라 가장 크게 둔다.
# 정확성은 코드 없이 '명백한 오류'만 판단할 수 있어 변별력이 낮으므로 작게 둔다.
AXIS_WEIGHTS: Mapping[str, int] = {
    "intent": 35,
    "depth": 25,
    "specificity": 25,
    "accuracy": 15,
}

# 콜백에 싣는 축 이름. 순서가 곧 화면 표시 순서다.
_AXIS_WIRE_NAMES: Mapping[str, str] = {
    "intent": "INTENT",
    "depth": "DEPTH",
    "specificity": "SPECIFICITY",
    "accuracy": "ACCURACY",
}


@dataclass(frozen=True)
class AxisLevels:
    """한 문항에 대한 축별 등급(0~4). accuracy 는 기술 내용이 없는 질문이면 None."""

    intent: int
    depth: int
    specificity: int
    accuracy: int | None

    def without_accuracy(self) -> AxisLevels:
        return AxisLevels(self.intent, self.depth, self.specificity, None)

    def as_dict(self) -> dict[str, int | None]:
        return {
            "intent": self.intent,
            "depth": self.depth,
            "specificity": self.specificity,
            "accuracy": self.accuracy,
        }


@dataclass(frozen=True)
class ScoreBreakdown:
    """종합 점수의 산출 근거. 사용자에게 '어느 축이 몇 점이라 최종 몇 점'을 보여주는 재료다.

    축 점수는 표시할 정수 그대로이고, total_score 는 그 정수들로 계산한다.
    그래야 사용자가 화면의 숫자로 직접 계산해도 최종 점수와 맞는다.
    """

    intent: int
    depth: int
    specificity: int
    accuracy: int | None  # 채점한 문항 전부가 기술 내용이 없으면 None
    weights: dict[str, int]  # 실제 적용된 가중치. 해당 없는 축이 빠지면 합이 100 이 아니다.
    total_score: int

    def axis_scores(self) -> dict[str, int | None]:
        return {
            "intent": self.intent,
            "depth": self.depth,
            "specificity": self.specificity,
            "accuracy": self.accuracy,
        }


@dataclass(frozen=True)
class SessionScores:
    """기존 콜백 3지표와 그 산출 근거. API 계약을 바꾸기 전까지 breakdown 은 로그로만 남긴다."""

    total_score: int
    intent_alignment_score: int
    reliability_score: int
    consistency: int | None
    breakdown: ScoreBreakdown

    def log_extra(self) -> dict[str, object]:
        return {
            "scoring_version": SCORING_VERSION,
            "axis_scores": self.breakdown.axis_scores(),
            "weights": self.breakdown.weights,
            "consistency": self.consistency,
        }

    def breakdown_payload(self) -> dict[str, object]:
        """콜백 overall.score_breakdown 에 싣는 값(파이썬 이름). 모양은 feedback.dto.ScoreBreakdownPayload."""
        axis_scores = self.breakdown.axis_scores()
        return {
            "scoring_version": SCORING_VERSION,
            "axes": [
                {
                    "axis": wire_name,
                    "score": axis_scores[axis],
                    "weight": self.breakdown.weights.get(axis),
                }
                for axis, wire_name in _AXIS_WIRE_NAMES.items()
            ],
            "consistency_score": self.consistency,
        }


def parse_axis_levels(raw: object) -> AxisLevels | None:
    """LLM 이 낸 scores 객체를 검증한다. 형식이 어긋나면 None.

    의도 충족이 0(동문서답·회피)이면 나머지 축도 0 으로 맞춘다.
    엉뚱한 질문에 깊이 있게 답한 경우를 점수로 인정하지 않기 위해서다.
    프롬프트에도 같은 규칙이 있지만, 모델이 어겨도 결과가 같도록 여기서 강제한다.
    """
    if not isinstance(raw, dict):
        return None

    intent = _level(raw.get("intent"))
    depth = _level(raw.get("depth"))
    specificity = _level(raw.get("specificity"))
    accuracy_raw = raw.get("accuracy")
    accuracy = None if accuracy_raw is None else _level(accuracy_raw)
    if intent is None or depth is None or specificity is None:
        return None
    if accuracy_raw is not None and accuracy is None:
        return None

    if intent == 0:
        return AxisLevels(0, 0, 0, None if accuracy is None else 0)
    return AxisLevels(intent, depth, specificity, accuracy)


def parse_consistency(raw: object) -> int | None:
    """세션 단위 일관성 등급. 판단할 수 없으면(null·형식 오류) None."""
    if raw is None:
        return None
    level = _level(raw)
    if level is None:
        logger.warning("feedback.scoring.invalid_consistency", extra={"value": raw})
    return level


def compute_breakdown(levels: Sequence[AxisLevels]) -> ScoreBreakdown | None:
    """문항별 등급을 축 점수와 종합 점수로 바꾼다. 채점된 문항이 없으면 None."""
    if not levels:
        return None

    intent = _axis_average([item.intent for item in levels])
    depth = _axis_average([item.depth for item in levels])
    specificity = _axis_average([item.specificity for item in levels])
    # 일부 문항만 해당 없음이면 그 문항만 평균에서 뺀다.
    accuracies = [item.accuracy for item in levels if item.accuracy is not None]
    accuracy = _axis_average(accuracies) if accuracies else None

    present = {"intent": intent, "depth": depth, "specificity": specificity}
    if accuracy is not None:
        present["accuracy"] = accuracy
    # 세션 전체에서 해당 없는 축은 빼고, 남은 축의 가중치 비율대로 나눈다.
    weights = {axis: AXIS_WEIGHTS[axis] for axis in present}
    weighted = sum(present[axis] * weight for axis, weight in weights.items())
    total = _round(weighted / sum(weights.values()))
    return ScoreBreakdown(intent, depth, specificity, accuracy, weights, total)


def summarize_session(levels: Sequence[AxisLevels], consistency_level: int | None) -> SessionScores | None:
    """세션 전체 점수. 채점된 문항이 없으면 None."""
    breakdown = compute_breakdown(levels)
    if breakdown is None:
        return None
    # 답변이 1개뿐이면 비교할 대상이 없어 일관성을 판단할 수 없다. 모델이 값을 줘도 버린다.
    consistency = consistency_score(consistency_level) if len(levels) > 1 else None
    return SessionScores(
        total_score=breakdown.total_score,
        intent_alignment_score=breakdown.intent,
        reliability_score=reliability_score(consistency, breakdown.specificity),
        consistency=consistency,
        breakdown=breakdown,
    )


def consistency_score(level: int | None) -> int | None:
    return None if level is None else level * LEVEL_POINTS


def reliability_score(consistency: int | None, specificity: int) -> int:
    """기존 reliabilityScore 필드용 값. 원래 정의(모순 없음 + 근거 구체성)를 그대로 잇는다.

    일관성만 쓰면 모순 없는 세션이 대부분이라 거의 늘 100 이 된다.
    일관성을 판단할 수 없으면(답변 1개 등) 구체성만 쓴다.
    """
    if consistency is None:
        return specificity
    return _round((consistency + specificity) / 2)


def _axis_average(levels: Sequence[int]) -> int:
    return _round(sum(levels) * LEVEL_POINTS / len(levels))


def _level(value: object) -> int | None:
    # bool 은 int 의 하위 타입이라 True 가 1 로 통과하는 것을 막는다.
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not 0 <= value <= MAX_LEVEL:
        return None
    return value


def _round(value: float) -> int:
    # 파이썬 round 는 은행가 반올림(0.5 → 짝수)이라 화면 숫자로 직접 계산한 값과 어긋날 수 있다.
    return math.floor(value + 0.5)
