from __future__ import annotations

from typing import Any

from app.core.common.audio.dto import (
    AudioAnswer,
    AudioPolicy,
    PreparedAnswerAudio,
    Region,
    Transcript,
)

PREPROCESSING_STAGES = ("source", "inspect", "normalize", "quality", "vad", "transcribe", "align")
# A dependency is ordered, but is not necessarily required to succeed (e.g. finalize).
DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "source": (),
    "inspect": ("source",),
    "normalize": ("source", "inspect"),
    "quality": ("normalize",),
    "vad": ("normalize",),
    "transcribe": ("normalize",),
    "align": ("normalize", "transcribe"),
    "prepare": PREPROCESSING_STAGES,
    "timing": ("prepare",),
    "fluency": ("prepare",),
}


def complement(speech: list[Region], duration_ms: int) -> list[Region]:
    """VAD regions must be sorted, disjoint, and within decoded audio."""
    cursor = 0
    gaps = []
    for region in speech:
        if region.start_ms < cursor or region.end_ms > duration_ms:
            raise ValueError("invalid speech timeline")
        if region.start_ms > cursor:
            gaps.append(Region(start_ms=cursor, end_ms=region.start_ms))
        cursor = region.end_ms
    if cursor < duration_ms:
        gaps.append(Region(start_ms=cursor, end_ms=duration_ms))
    return gaps


def align_timestamps(transcript: Transcript, duration_ms: int) -> Transcript:
    """Validate, don't invent or clamp timestamps or snap them to VAD boundaries."""
    previous_end = 0
    words = []
    for original in transcript.words:
        word = original.model_copy()
        start, end = word.start_ms, word.end_ms
        if start is None or end is None:
            word.timing_status = "missing"
        elif not previous_end <= start < end <= duration_ms:
            word.timing_status = "invalid"
        else:
            word.timing_status = "valid"
            previous_end = end
        words.append(word)
    return transcript.model_copy(update={"words": words})


def assemble_prepared(
    answer: AudioAnswer, policy: AudioPolicy, stages: dict[str, dict[str, Any]]
) -> PreparedAnswerAudio:
    statuses = {name: stages[name]["status"] for name in PREPROCESSING_STAGES}

    def output(name: str) -> dict[str, Any]:
        entry = stages[name]
        return dict(entry["result"]) if entry["status"] == "succeeded" else {}

    normalized = output("normalize")
    quality = output("quality")
    frames = quality.pop("frames", None)
    if frames is not None:
        quality["frame_count"] = len(frames)
        quality["details_stage"] = "quality"
    vad = output("vad")
    aligned = output("align")
    raw_transcript = aligned or output("transcribe")
    speech = [Region.model_validate(item) for item in vad["speech"]] if "speech" in vad else None
    duration = normalized.get("duration_ms")
    status = "ready" if all(value == "succeeded" for value in statuses.values()) else "partial"
    if not normalized:
        status = "unusable"
    limitations = ["ASR may omit fillers and repetitions; VAD non-speech is not proof of silence."]
    if not aligned:
        limitations.append("Validated word timing unavailable.")
    versions = {name: str(output(name).get("implementation", "")) for name in PREPROCESSING_STAGES}
    return PreparedAnswerAudio(
        answer_id=answer.answer_id,
        question_id=answer.question_id,
        source_checksum=output("source").get("sha256", answer.sha256),
        policy_fingerprint=policy.fingerprint(),
        status=status,  # type: ignore[arg-type]
        duration_ms=duration,
        normalized_ref=normalized.get("path"),
        quality=quality or None,
        speech=speech,
        non_speech=complement(speech, duration) if speech is not None and duration is not None else None,
        transcript=Transcript.model_validate(raw_transcript) if raw_transcript else None,
        stage_statuses=statuses,
        stage_errors={name: stages[name]["error"] for name in PREPROCESSING_STAGES if stages[name].get("error")},
        versions=versions,
        limitations=limitations,
    )
