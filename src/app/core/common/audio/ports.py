from __future__ import annotations

from typing import Any, Protocol

from app.core.common.audio.dto import AudioAnswer, AudioPolicy, AudioRequest


class AudioStageBackend(Protocol):
    async def execute(
        self, stage: str, answer: AudioAnswer, policy: AudioPolicy, inputs: dict[str, dict[str, Any]]
    ) -> dict[str, Any]: ...


class AudioRepository(Protocol):
    def submit(self, request: AudioRequest, policy: AudioPolicy) -> str: ...

    def snapshot(self, job_id: str) -> dict[str, Any] | None: ...

    def claim(self, capacities: dict[str, int], lease_seconds: int) -> dict[str, Any] | None: ...

    def heartbeat(self, task_id: str, token: str, lease_seconds: int) -> bool: ...

    def context(self, task: dict[str, Any]) -> dict[str, Any]: ...

    def finish(self, task: dict[str, Any], result: dict[str, Any]) -> bool: ...

    def fail(self, task: dict[str, Any], code: str, *, retryable: bool) -> bool: ...
