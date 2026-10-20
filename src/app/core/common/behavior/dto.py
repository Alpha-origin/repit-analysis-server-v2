from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, model_validator

from app.core.common.behavior.validation import validate_data
from app.core.common.video.dto import WireModel

Milliseconds = Annotated[int, Field(ge=0, le=3_600_000)]
Ratio = Annotated[float, Field(ge=0, le=1)]
Reason = Literal[
    "INSUFFICIENT_DURATION",
    "NO_SINGLE_FACE",
    "HEAD_POSE_UNAVAILABLE",
    "SHOULDERS_NOT_VISIBLE",
    "BLENDSHAPES_UNAVAILABLE",
    "LOW_COVERAGE",
]
WarningCode = Literal[
    "LOW_SAMPLE_COVERAGE",
    "MULTIPLE_PEOPLE",
    "FACE_TOO_SMALL",
    "LOW_FACE_COVERAGE",
    "LOW_POSE_COVERAGE",
    "COMPONENT_FAILURE",
    "EVENTS_TRUNCATED",
]
EventType = Literal["FACE_NOT_VISIBLE", "HEAD_TURNED", "SHOULDER_TILTED", "UPPER_BODY_MOVEMENT"]


class BehaviorModel(WireModel):
    model_config = ConfigDict(strict=True)


class DurationPair(BehaviorModel):
    numerator_ms: Milliseconds
    denominator_ms: Milliseconds

    @model_validator(mode="after")
    def ordered(self) -> DurationPair:
        if self.numerator_ms > self.denominator_ms:
            raise ValueError("numerator exceeds denominator")
        return self


class Metric(DurationPair):
    status: Literal["AVAILABLE", "UNAVAILABLE"]
    value: Ratio | None
    coverage_ratio: Ratio
    unavailable_reason: Reason | None


class TimelineBin(BehaviorModel):
    start_ms: Milliseconds
    end_ms: Milliseconds
    metrics: Annotated[dict[str, DurationPair], Field(min_length=5, max_length=5)]


class Event(BehaviorModel):
    type: EventType
    source_metric: str
    start_ms: Milliseconds
    end_ms: Milliseconds


class BehaviorData(BehaviorModel):
    schema_version: Literal["behavior-v1"] = "behavior-v1"
    model_version: Annotated[str, Field(min_length=1, max_length=100)]
    thresholds_version: Literal["behavior-policy-v1"] = "behavior-policy-v1"
    video_id: Annotated[str, Field(min_length=1, max_length=200)]
    duration_ms: Annotated[int, Field(ge=1, le=3_600_000)]
    sample_interval_ms: Literal[200] = 200
    sampled_frame_count: Annotated[int, Field(ge=0, le=18_000)]
    sampled_duration_ms: Milliseconds
    metrics: Annotated[dict[str, Metric], Field(min_length=5, max_length=5)]
    timeline: Annotated[list[TimelineBin], Field(min_length=1, max_length=60)]
    events: Annotated[list[Event], Field(max_length=60)]
    events_truncated: bool
    summary: Annotated[list[Annotated[str, Field(min_length=1, max_length=200)]], Field(max_length=3)]
    warnings: Annotated[list[WarningCode], Field(max_length=8)]

    @model_validator(mode="after")
    def coherent(self) -> BehaviorData:
        validate_data(self)
        return self
