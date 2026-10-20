from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from app.core.common.behavior.policy import (
    EVENT_METRICS,
    KEYS,
    MIN_COVERAGE,
    MIN_DURATION_MS,
    MIN_EVENT_MS,
    READY_COVERAGE,
    READY_SAMPLE_COVERAGE,
    SAMPLE_MS,
    bin_width,
    fraction,
)

if TYPE_CHECKING:
    from app.core.common.behavior.dto import BehaviorData, Metric


ROUNDING_TOLERANCE = 1e-6


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def available(denominator: int, duration: int) -> bool:
    return duration >= MIN_DURATION_MS and denominator >= MIN_DURATION_MS and denominator / duration >= MIN_COVERAGE


def quality(data: BehaviorData) -> Literal["ready", "partial"]:
    ready = (
        all(
            m.status == "AVAILABLE" and m.denominator_ms / data.duration_ms >= READY_COVERAGE
            for m in data.metrics.values()
        )
        and data.sampled_duration_ms / data.duration_ms >= READY_SAMPLE_COVERAGE
        and "COMPONENT_FAILURE" not in data.warnings
    )
    return "ready" if ready else "partial"


def _metric(key: str, metric: Metric, data: BehaviorData) -> None:
    denominator = metric.denominator_ms
    require(denominator <= data.sampled_duration_ms, "metric exceeds sampled duration")
    require(
        abs(metric.coverage_ratio - fraction(denominator, data.duration_ms)) <= ROUNDING_TOLERANCE, "coverage mismatch"
    )
    measurable = available(denominator, data.duration_ms)
    require((metric.status == "AVAILABLE") == measurable, "metric status mismatch")
    if measurable:
        require(metric.value is not None and metric.unavailable_reason is None, "available metric missing value")
        if metric.value is not None:
            require(
                abs(metric.value - fraction(metric.numerator_ms, denominator)) <= ROUNDING_TOLERANCE, "value mismatch"
            )
    else:
        require(metric.value is None and metric.unavailable_reason is not None, "unavailable metric needs reason")
        if data.duration_ms < MIN_DURATION_MS:
            reason = "INSUFFICIENT_DURATION"
        elif key != KEYS[0] and data.metrics[KEYS[0]].numerator_ms == 0:
            reason = "NO_SINGLE_FACE"
        elif denominator == 0:
            reason = {
                KEYS[1]: "HEAD_POSE_UNAVAILABLE",
                KEYS[2]: "SHOULDERS_NOT_VISIBLE",
                KEYS[3]: "SHOULDERS_NOT_VISIBLE",
                KEYS[4]: "BLENDSHAPES_UNAVAILABLE",
            }.get(key, "LOW_COVERAGE")
        else:
            reason = "LOW_COVERAGE"
        require(metric.unavailable_reason == reason, "metric unavailable reason mismatch")
    if key != KEYS[0]:
        require(denominator <= data.metrics[KEYS[0]].numerator_ms, "metric exceeds single-face duration")


def _timeline(data: BehaviorData) -> None:
    width = bin_width(data.duration_ms)
    require(len(data.timeline) == (data.duration_ms + width - 1) // width, "timeline size mismatch")
    totals = {key: [0, 0] for key in KEYS}
    for index, item in enumerate(data.timeline):
        require(item.start_ms == index * width, "timeline gap")
        require(item.end_ms == min((index + 1) * width, data.duration_ms), "timeline boundary mismatch")
        require(set(item.metrics) == set(KEYS), "unknown timeline metric")
        for key, pair in item.metrics.items():
            require(pair.denominator_ms <= item.end_ms - item.start_ms, "bin duration overflow")
            totals[key][0] += pair.numerator_ms
            totals[key][1] += pair.denominator_ms
    for key, metric in data.metrics.items():
        require(totals[key] == [metric.numerator_ms, metric.denominator_ms], "timeline sum mismatch")


def _events(data: BehaviorData) -> None:
    require(data.events == sorted(data.events, key=lambda e: (e.start_ms, e.type, e.end_ms)), "event order mismatch")
    ends: dict[str, int] = {}
    for event in data.events:
        require(EVENT_METRICS[event.type] == event.source_metric, "event metric mismatch")
        require(data.metrics[event.source_metric].status == "AVAILABLE", "event for unavailable metric")
        require(0 <= event.start_ms < event.end_ms <= data.duration_ms, "invalid event range")
        require(event.end_ms - event.start_ms >= MIN_EVENT_MS, "event too short")
        require(ends.get(event.type, 0) <= event.start_ms, "overlapping event")
        ends[event.type] = event.end_ms
    for item in data.timeline:
        for event_type, key in EVENT_METRICS.items():
            marked = sum(
                max(0, min(e.end_ms, item.end_ms) - max(e.start_ms, item.start_ms))
                for e in data.events
                if e.type == event_type
            )
            pair = item.metrics[key]
            require(marked <= pair.denominator_ms - pair.numerator_ms, "event contradicts timeline")


def validate_data(data: BehaviorData) -> None:
    require(set(data.metrics) == set(KEYS), "unknown metric keys")
    count, duration, sampled = data.sampled_frame_count, data.duration_ms, data.sampled_duration_ms
    require(count <= (duration + SAMPLE_MS - 1) // SAMPLE_MS, "sample count overflow")
    require((count == 0) == (sampled == 0), "sample count/duration mismatch")
    require(max(0, (count - 1) * SAMPLE_MS) <= sampled <= min(duration, count * SAMPLE_MS), "slot weights mismatch")
    for key, metric in data.metrics.items():
        _metric(key, metric, data)
    require(any(m.status == "AVAILABLE" for m in data.metrics.values()), "empty successful result")
    _timeline(data)
    _events(data)
    require(data.warnings == sorted(set(data.warnings)), "warning order or duplicates")
    require(("EVENTS_TRUNCATED" in data.warnings) == data.events_truncated, "truncation mismatch")
    require(
        ("LOW_SAMPLE_COVERAGE" in data.warnings) == (sampled / duration < READY_SAMPLE_COVERAGE), "sampling warning"
    )
    for warning, keys in (("LOW_FACE_COVERAGE", (KEYS[1], KEYS[4])), ("LOW_POSE_COVERAGE", KEYS[2:4])):
        low = any(data.metrics[key].denominator_ms / duration < READY_COVERAGE for key in keys)
        require((warning in data.warnings) == low, "coverage warning mismatch")
