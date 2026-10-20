from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import dist
from statistics import median

from app.core.common.behavior.policy import (
    HEAD_ANGLE_DEGREES,
    KEYS,
    SHOULDER_ANGLE_DEGREES,
    SMILE_THRESHOLD,
    STABLE_SPEED,
)


@dataclass(frozen=True)
class Observation:
    slot: int
    timestamp_ms: int
    face_count: int | None
    pose_count: int | None = None
    yaw: float | None = None
    pitch: float | None = None
    tilt: float | None = None
    center: tuple[float, float] | None = None
    shoulder_width: float | None = None
    smile: float | None = None
    face_small: bool = False
    component_failure: bool = False


class ObservationFilter:
    def __init__(self) -> None:
        self._windows: dict[str, deque[float]] = {}
        self._previous: Observation | None = None

    def _smooth(self, key: str, value: float | None) -> float | None:
        if value is None:
            self._windows.pop(key, None)
            return None
        window = self._windows.setdefault(key, deque(maxlen=3))
        window.append(value)
        return float(median(window))

    def flags(self, current: Observation) -> dict[str, bool | None]:
        previous = self._previous
        if previous is None or current.slot != previous.slot + 1:
            self._windows.clear()
            previous = None
        single = current.face_count == 1
        yaw = self._smooth("yaw", current.yaw if single and not current.face_small else None)
        pitch = self._smooth("pitch", current.pitch if single and not current.face_small else None)
        tilt = self._smooth("tilt", current.tilt if single and current.pose_count == 1 else None)
        smile = self._smooth("smile", current.smile if single else None)
        speed = self._smooth("speed", _speed(previous, current))
        self._previous = current
        return {
            KEYS[0]: single if current.face_count in (0, 1) else None,
            KEYS[1]: abs(yaw) <= HEAD_ANGLE_DEGREES and abs(pitch) <= HEAD_ANGLE_DEGREES
            if yaw is not None and pitch is not None
            else None,
            KEYS[2]: tilt <= SHOULDER_ANGLE_DEGREES if tilt is not None else None,
            KEYS[3]: speed <= STABLE_SPEED if speed is not None else None,
            KEYS[4]: smile >= SMILE_THRESHOLD if smile is not None else None,
        }


def _speed(previous: Observation | None, current: Observation) -> float | None:
    if previous is None:
        return None
    for sample in (previous, current):
        if sample.face_count != 1 or sample.pose_count != 1 or sample.center is None or sample.shoulder_width is None:
            return None
    delta = current.timestamp_ms - previous.timestamp_ms
    if not 0 < delta <= 2 * 200:
        return None
    if (
        current.center is None
        or previous.center is None
        or current.shoulder_width is None
        or previous.shoulder_width is None
    ):
        return None
    width = (current.shoulder_width + previous.shoulder_width) / 2
    if width <= 0:
        return None
    distance = dist(current.center, previous.center)
    return distance / width / (delta / 1_000)
