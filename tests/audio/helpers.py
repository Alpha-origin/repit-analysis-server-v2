from __future__ import annotations

from collections import Counter
from typing import Any

from app.core.common.audio.dto import AudioAnswer, AudioError, AudioPolicy, AudioRequest, TimedWord, Transcript
from app.core.common.audio.preprocessing import align_timestamps
from app.core.common.interview_qa.ports.anthropic_text_client import AnthropicCallResult


def request(count: int = 1) -> AudioRequest:
    return AudioRequest(
        request_id="request-1",
        session_id="session-1",
        answers=[
            AudioAnswer(
                answer_id=f"a-{i}", question_id=f"q-{i}", asset_key=f"{i}.wav", sha256="a" * 64, end_reason="user"
            )
            for i in range(count)
        ],
    )


def transcript() -> Transcript:
    return Transcript(
        text="안녕하세요 반갑습니다 잘부탁합니다 감사합니다",
        model="fake",
        words=[
            TimedWord(text="안녕하세요", start_ms=1100, end_ms=2000),
            TimedWord(text="반갑습니다", start_ms=5000, end_ms=6000),
            TimedWord(text="잘부탁합니다", start_ms=9000, end_ms=10000),
            TimedWord(text="감사합니다", start_ms=13000, end_ms=14000),
        ],
    )


class FakeBackend:
    def __init__(self, fail: str | None = None) -> None:
        self.calls: Counter[str] = Counter()
        self.fail = fail

    async def execute(  # noqa: PLR0911 — mirrors independent stage implementations
        self,
        stage: str,
        answer: AudioAnswer,
        policy: AudioPolicy,
        inputs: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        self.calls[stage] += 1
        if stage == self.fail:
            raise AudioError("test_failure")
        if stage == "source":
            return {"path": "source", "sha256": answer.sha256}
        if stage == "inspect":
            return {"channels": 1}
        if stage == "normalize":
            return {"path": "normalized", "duration_ms": 16000}
        if stage == "quality":
            return {"peak": 0.5}
        if stage == "vad":
            return {"speech": [{"start_ms": 1000, "end_ms": 7000}, {"start_ms": 8000, "end_ms": 15000}]}
        if stage == "transcribe":
            return transcript().model_dump()
        if stage == "align":
            return align_timestamps(Transcript.model_validate(inputs["transcribe"]), 16000).model_dump()
        raise AssertionError(stage)


class FakeClient:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.calls = 0
        self.payload = payload or {
            "observations": [],
            "final_sentence": "complete",
            "final_sentence_evidence": "감사합니다",
            "comment": "전사 근거에 한정됨",
        }

    async def call(self, **kwargs: Any) -> AnthropicCallResult:
        self.calls += 1
        return AnthropicCallResult(
            content_blocks=[
                {
                    "type": "tool_use",
                    "name": "submit_audio_fluency",
                    "input": self.payload,
                }
            ],
            input_tokens=0,
            output_tokens=0,
        )


class FakeWebhook:
    def __init__(self, success: bool = True) -> None:
        self.calls = 0
        self.success = success
        self.payloads: list[dict[str, Any]] = []

    async def send(self, url: str, payload: dict[str, Any]) -> bool:
        self.calls += 1
        self.payloads.append(payload)
        return self.success
