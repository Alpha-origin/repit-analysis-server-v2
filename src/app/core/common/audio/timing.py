from __future__ import annotations

import re
import statistics
from typing import Any

from app.core.common.audio.dto import AudioPolicy, PreparedAnswerAudio, Region

MINIMUM_WINDOWS = 2


def syllables(text: str) -> int:
    """Orthographic Hangul syllables only: never count ASR tokens as spoken syllables."""
    return len(re.findall(r"[가-힣]", text))


def rate(count: int, milliseconds: int) -> float | None:
    return round(count * 1000 / milliseconds, 4) if milliseconds > 0 else None


def overlap(region: Region, start: int, end: int) -> int:
    return max(0, min(region.end_ms, end) - max(region.start_ms, start))


def analyze_timing(  # noqa: C901, PLR0911 — each unavailable measurement has its own reason
    prepared: PreparedAnswerAudio,
    policy: AudioPolicy,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "version": "timing-v1",
        "status": "unavailable",
        "silence": None,
        "speed": None,
        "stability": None,
        "limitations": list(prepared.limitations),
        "measurement_policy": {
            "unit": "written_hangul_syllables_per_second",
            "include_transcribed_fillers_and_repetitions": True,
            "pause_min_ms": policy.pause_min_ms,
            "window_ms": policy.window_ms,
        },
    }
    if prepared.speech is None or prepared.duration_ms is None:
        result["reason"] = "speech_activity_unavailable"
        return result
    speech = prepared.speech
    voiced_ms = sum(region.end_ms - region.start_ms for region in speech)
    gaps = prepared.non_speech or []
    if not speech:
        result.update(
            status="partial",
            reason="no_speech_detected",
            silence={"whole_file_non_speech_ms": prepared.duration_ms, "internal_pauses": []},
        )
        return result
    first, last = speech[0].start_ms, speech[-1].end_ms
    internal = [gap for gap in gaps if first <= gap.start_ms and gap.end_ms <= last]
    pauses = [gap for gap in internal if gap.end_ms - gap.start_ms >= policy.pause_min_ms]
    span = last - first
    result["silence"] = {
        "speech_ms": voiced_ms,
        "leading_non_speech_ms": first,
        "trailing_non_speech_ms": prepared.duration_ms - last,
        "internal_non_speech_ms": span - voiced_ms,
        "internal_non_speech_ratio": (span - voiced_ms) / span,
        "internal_pauses": [gap.model_dump() for gap in pauses],
        "pause_count": len(pauses),
        "thresholded_pause_ms": sum(gap.end_ms - gap.start_ms for gap in pauses),
    }
    result["status"] = "partial"
    transcript = prepared.transcript
    if transcript is None or not transcript.text.strip():
        result["reason"] = "transcript_unavailable_or_empty"
        return result
    if re.search(r"[A-Za-z0-9ㄱ-ㅎㅏ-ㅣ]", transcript.text):
        result["limitations"].append("Non-Hangul speech is not counted; rates are partial text-based estimates.")
    count = syllables(transcript.text)
    if not count:
        result["reason"] = "no_countable_hangul_syllables"
        return result
    result["speed"] = {
        "syllable_count": count,
        "including_internal_pauses": rate(count, span),
        "excluding_vad_non_speech": rate(count, voiced_ms),
    }
    # All counted syllables need valid timing; partial timing would bias local rates.
    words = transcript.words
    valid = [word for word in words if word.timing_status == "valid"]
    if not valid or any(word.timing_status != "valid" and syllables(word.text) for word in words):
        result["reason"] = "word_timing_incomplete"
        return result
    if sum(syllables(word.text) for word in valid) != count:
        result["reason"] = "word_text_coverage_mismatch"
        return result
    if any(
        word.start_ms is not None and word.end_ms is not None and (word.start_ms < first or word.end_ms > last)
        for word in valid
        if syllables(word.text)
    ):
        result["reason"] = "word_timing_outside_speech_span"
        return result
    windows = []
    # Keep full windows only. Assign each word by midpoint; don't fabricate phoneme timing.
    for start in range(first, last - policy.window_ms + 1, policy.window_ms):
        end = start + policy.window_ms
        active_ms = sum(overlap(region, start, end) for region in speech)
        if active_ms < policy.minimum_window_speech_ms:
            continue
        amount = sum(
            syllables(word.text)
            for word in valid
            if word.start_ms is not None
            and word.end_ms is not None
            and start <= (word.start_ms + word.end_ms) / 2 < end
        )
        windows.append({"start_ms": start, "end_ms": end, "rate": rate(amount, policy.window_ms)})
    if len(windows) < MINIMUM_WINDOWS:
        result["reason"] = "insufficient_full_windows"
        return result
    rates = [float(window["rate"]) for window in windows if window["rate"] is not None]
    mean = statistics.mean(rates)
    deviation = statistics.pstdev(rates)
    result["stability"] = {
        "windows": windows,
        "mean": mean,
        "standard_deviation": deviation,
        "coefficient_of_variation": deviation / mean if mean else None,
        "method": "full_fixed_windows_including_pauses_word_midpoint_assignment",
    }
    result["status"] = "ready"
    return result
