from __future__ import annotations

from typing import cast

from app.core.common.behavior.dto import BehaviorData, DurationPair, Metric, Reason, TimelineBin, WarningCode
from app.core.common.behavior.events import EventCollector
from app.core.common.behavior.observations import Observation, ObservationFilter
from app.core.common.behavior.policy import (
    KEYS,
    MAX_DURATION_MS,
    MIN_DURATION_MS,
    MODEL_VERSION,
    READY_COVERAGE,
    READY_SAMPLE_COVERAGE,
    SAMPLE_MS,
    SUMMARY_TEMPLATES,
    bin_width,
    fraction,
    percentage,
)
from app.core.common.behavior.validation import available, quality
from app.core.common.video.dto import AnalysisOutcome, public_error
from app.core.common.video.errors import PublicErrorCode


class BehaviorAggregator:
    def __init__(self, video_id: str, duration_ms: int, model_version: str = MODEL_VERSION) -> None:
        if not 0 < duration_ms <= MAX_DURATION_MS:
            raise ValueError("invalid behavior duration")
        self.video_id, self.duration, self.model_version = video_id, duration_ms, model_version
        self.width = bin_width(duration_ms)
        self._bins = [{key: [0, 0] for key in KEYS} for _ in range((duration_ms + self.width - 1) // self.width)]
        self._totals = {key: [0, 0] for key in KEYS}
        self._filter = ObservationFilter()
        self._events = EventCollector()
        self._warnings: set[str] = set()
        self._last_slot, self._last_timestamp = -1, -1
        self._count, self._sampled, self._single_face = 0, 0, 0

    def add(self, observation: Observation) -> None:
        start = observation.slot * SAMPLE_MS
        if not self._last_slot < observation.slot or not 0 <= start <= observation.timestamp_ms < min(
            start + SAMPLE_MS, self.duration
        ):
            raise ValueError("invalid observation slot")
        if observation.timestamp_ms <= self._last_timestamp:
            raise ValueError("observation timestamps must increase")
        end = min(start + SAMPLE_MS, self.duration)
        self._last_slot, self._last_timestamp = observation.slot, observation.timestamp_ms
        self._count += 1
        self._sampled += end - start
        if observation.face_count == 1:
            self._single_face += end - start
        for condition, warning in (
            (observation.face_count is not None and observation.face_count > 1, "MULTIPLE_PEOPLE"),
            (observation.pose_count is not None and observation.pose_count > 1, "MULTIPLE_PEOPLE"),
            (observation.face_small, "FACE_TOO_SMALL"),
            (observation.component_failure, "COMPONENT_FAILURE"),
        ):
            if condition:
                self._warnings.add(warning)
        flags = self._filter.flags(observation)
        self._events.add(start, end, flags)
        for key, flag in flags.items():
            if flag is None:
                continue
            self._totals[key][1] += end - start
            self._totals[key][0] += (end - start) if flag else 0
            for index in range(start // self.width, (end - 1) // self.width + 1):
                weight = min(end, (index + 1) * self.width) - max(start, index * self.width)
                self._bins[index][key][1] += weight
                self._bins[index][key][0] += weight if flag else 0

    def _reason(self, key: str, denominator: int) -> Reason:
        if self.duration < MIN_DURATION_MS:
            return "INSUFFICIENT_DURATION"
        if key != KEYS[0] and self._single_face == 0:
            return "NO_SINGLE_FACE"
        if denominator == 0:
            reasons: dict[str, Reason] = {
                KEYS[1]: "HEAD_POSE_UNAVAILABLE",
                KEYS[2]: "SHOULDERS_NOT_VISIBLE",
                KEYS[3]: "SHOULDERS_NOT_VISIBLE",
                KEYS[4]: "BLENDSHAPES_UNAVAILABLE",
            }
            return reasons.get(key, "LOW_COVERAGE")
        return "LOW_COVERAGE"

    def finish(self) -> AnalysisOutcome:
        metrics = {}
        for key, (numerator, denominator) in self._totals.items():
            measured = available(denominator, self.duration)
            metrics[key] = Metric(
                numerator_ms=numerator,
                denominator_ms=denominator,
                status="AVAILABLE" if measured else "UNAVAILABLE",
                value=fraction(numerator, denominator) if measured else None,
                coverage_ratio=fraction(denominator, self.duration),
                unavailable_reason=None if measured else self._reason(key, denominator),
            )
        available_keys = {key for key, metric in metrics.items() if metric.status == "AVAILABLE"}
        if not available_keys:
            code: PublicErrorCode = (
                "INTERNAL_ERROR" if "COMPONENT_FAILURE" in self._warnings else "BEHAVIOR_NOT_MEASURABLE"
            )
            return AnalysisOutcome(status="unavailable", data=None, error=public_error(code))
        events = self._events.finish(available_keys)
        if self._events.truncated:
            self._warnings.add("EVENTS_TRUNCATED")
        if self._sampled / self.duration < READY_SAMPLE_COVERAGE:
            self._warnings.add("LOW_SAMPLE_COVERAGE")
        for warning, keys in (("LOW_FACE_COVERAGE", (KEYS[1], KEYS[4])), ("LOW_POSE_COVERAGE", KEYS[2:4])):
            if any(metrics[key].denominator_ms / self.duration < READY_COVERAGE for key in keys):
                self._warnings.add(warning)
        summary = [
            template.format(p=percentage(metrics[key].numerator_ms, metrics[key].denominator_ms))
            for key, template in zip(KEYS, SUMMARY_TEMPLATES, strict=True)
            if key in available_keys
        ][:3]
        data = BehaviorData(
            model_version=self.model_version,
            video_id=self.video_id,
            duration_ms=self.duration,
            sampled_frame_count=self._count,
            sampled_duration_ms=self._sampled,
            metrics=metrics,
            timeline=[
                TimelineBin(
                    start_ms=i * self.width,
                    end_ms=min((i + 1) * self.width, self.duration),
                    metrics={key: DurationPair(numerator_ms=n, denominator_ms=d) for key, (n, d) in pairs.items()},
                )
                for i, pairs in enumerate(self._bins)
            ],
            events=events,
            events_truncated=self._events.truncated,
            summary=summary,
            warnings=cast("list[WarningCode]", sorted(self._warnings)),
        )
        return AnalysisOutcome(status=quality(data), data=data.wire(), error=None)
