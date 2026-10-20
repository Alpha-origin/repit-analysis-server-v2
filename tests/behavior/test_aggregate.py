from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from app.core.common.behavior.aggregate import BehaviorAggregator
from app.core.common.behavior.dto import BehaviorData
from app.core.common.behavior.events import EventCollector
from app.core.common.behavior.observations import Observation, ObservationFilter
from app.core.common.behavior.policy import KEYS


def sample(slot: int, **changes: Any) -> Observation:
    base = Observation(slot, slot * 200, 1, 1, yaw=0, pitch=0, tilt=0, center=(100, 100), shoulder_width=100, smile=0.6)
    return replace(base, **changes)


def finish(duration: int = 20_000, **changes: Any) -> tuple[str, BehaviorData | None]:
    aggregate = BehaviorAggregator("501", duration)
    for slot in range((duration + 199) // 200):
        aggregate.add(sample(slot, **changes))
    outcome = aggregate.finish()
    return outcome.status, BehaviorData.model_validate(outcome.data) if outcome.data is not None else None


def test_ready_is_time_weighted_and_first_motion_slot_is_unknown() -> None:
    status, data = finish(20_105)
    assert status == "ready"
    assert data is not None
    assert data.sampled_duration_ms == 20_105
    assert data.sampled_frame_count == 101
    assert data.metrics[KEYS[3]].denominator_ms == 19_905
    assert data.metrics[KEYS[0]].value == 1
    assert data.events == []
    assert len(data.summary) == 3


def test_zero_face_is_partial_not_fake_zero_for_other_metrics() -> None:
    status, data = finish(face_count=0)
    assert status == "partial"
    assert data is not None
    assert data.metrics[KEYS[0]].value == 0
    assert all(data.metrics[key].value is None for key in KEYS[1:])
    assert data.events[0].start_ms == 0
    assert data.events[0].end_ms == 20_000


@pytest.mark.parametrize("duration", [1, 2000, 9999])
def test_short_video_is_not_measurable(duration: int) -> None:
    aggregate = BehaviorAggregator("501", duration)
    aggregate.add(sample(0))
    outcome = aggregate.finish()
    assert outcome.status == "unavailable"
    assert outcome.data is None
    assert outcome.error is not None
    assert outcome.error.code == "BEHAVIOR_NOT_MEASURABLE"
    assert outcome.error.retryable is False


def test_cropped_shoulders_and_component_failure_preserve_face() -> None:
    status, data = finish(pose_count=0, tilt=None, center=None, shoulder_width=None, component_failure=True)
    assert status == "partial"
    assert data is not None
    assert data.metrics[KEYS[1]].value == 1
    assert data.metrics[KEYS[2]].unavailable_reason == "SHOULDERS_NOT_VISIBLE"
    assert "COMPONENT_FAILURE" in data.warnings


def test_sparse_slots_do_not_fill_missing_time_or_bridge_events() -> None:
    aggregate = BehaviorAggregator("501", 40_000)
    for slot in range(200):
        if slot % 2 == 0:
            aggregate.add(sample(slot, face_count=0))
    outcome = aggregate.finish()
    data = BehaviorData.model_validate(outcome.data)
    assert outcome.status == "partial"
    assert data.sampled_duration_ms == 20_000
    assert data.metrics[KEYS[0]].coverage_ratio == 0.5
    assert data.events == []


def test_coverage_and_exact_ten_second_boundary() -> None:
    status, data = finish(10_000)
    assert status == "partial"
    assert data is not None
    assert data.metrics[KEYS[3]].unavailable_reason == "LOW_COVERAGE"
    status, data = finish(10_200)
    assert status == "ready"
    assert data is not None


def test_median_reset_prevents_stale_head_direction() -> None:
    filtered = ObservationFilter()
    filtered.flags(sample(0))
    filtered.flags(sample(1))
    assert filtered.flags(sample(3, yaw=50))[KEYS[1]] is False
    assert filtered.flags(sample(4, face_count=2))[KEYS[1]] is None
    assert filtered.flags(sample(5, yaw=50))[KEYS[1]] is False


def test_event_limit_does_not_drop_available_events_for_unavailable_metric() -> None:
    collector = EventCollector()
    for index in range(65):
        start = index * 4000
        flags = dict.fromkeys(KEYS)
        flags[KEYS[1]] = False
        collector.add(start, start + 2000, flags)
        collector.add(start + 2000, start + 4000, dict.fromkeys(KEYS))
    collector.add(300_000, 302_000, {key: False if key == KEYS[0] else None for key in KEYS})
    events = collector.finish({KEYS[0]})
    assert len(events) == 1
    assert events[0].type == "FACE_NOT_VISIBLE"
    assert collector.truncated is False


def test_reject_duplicate_or_misassigned_observation() -> None:
    aggregate = BehaviorAggregator("501", 20_000)
    aggregate.add(sample(0))
    with pytest.raises(ValueError, match="slot"):
        aggregate.add(sample(0))
    with pytest.raises(ValueError, match="slot"):
        aggregate.add(sample(1, timestamp_ms=199))


def test_failed_face_component_without_measurements_is_not_bad_user_media() -> None:
    aggregate = BehaviorAggregator("501", 20_000)
    for slot in range(100):
        aggregate.add(sample(slot, face_count=None, component_failure=True))
    outcome = aggregate.finish()
    assert outcome.status == "unavailable"
    assert outcome.error is not None
    assert outcome.error.code == "INTERNAL_ERROR"
