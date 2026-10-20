from __future__ import annotations

from typing import cast

from app.core.common.behavior.dto import Event, EventType
from app.core.common.behavior.policy import EVENT_METRICS, MAX_EVENTS, MIN_EVENT_MS


class EventCollector:
    """Retain only the earliest sixty events, including long runs that finish late."""

    def __init__(self) -> None:
        self._open: dict[str, tuple[int, int]] = {}
        self._finished: dict[str, list[Event]] = {key: [] for key in EVENT_METRICS}
        self._overflow: set[str] = set()
        self.truncated = False

    def add(self, start: int, end: int, flags: dict[str, bool | None]) -> None:
        for event_type, key in EVENT_METRICS.items():
            run = self._open.get(event_type)
            if run is not None and (run[1] != start or flags[key] is not False):
                self._close(event_type)
                run = None
            if flags[key] is False:
                self._open[event_type] = (run[0] if run is not None else start, end)

    def _close(self, event_type: str) -> None:
        start, end = self._open.pop(event_type)
        if end - start < MIN_EVENT_MS:
            return
        retained = self._finished[event_type]
        retained.append(
            Event(
                type=cast("EventType", event_type), source_metric=EVENT_METRICS[event_type], start_ms=start, end_ms=end
            )
        )
        retained.sort(key=lambda event: (event.start_ms, event.type, event.end_ms))
        if len(retained) > MAX_EVENTS:
            self._overflow.add(event_type)
            retained.pop()

    def finish(self, available_keys: set[str]) -> list[Event]:
        for event_type in list(self._open):
            self._close(event_type)
        candidates = [
            event for events in self._finished.values() for event in events if event.source_metric in available_keys
        ]
        candidates.sort(key=lambda event: (event.start_ms, event.type, event.end_ms))
        self.truncated = len(candidates) > MAX_EVENTS or any(
            EVENT_METRICS[kind] in available_keys for kind in self._overflow
        )
        return candidates[:MAX_EVENTS]
