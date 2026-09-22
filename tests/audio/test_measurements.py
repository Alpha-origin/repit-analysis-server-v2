from __future__ import annotations

import pytest

from app.core.common.audio.dto import AudioError, AudioPolicy, PreparedAnswerAudio, Region, TimedWord, Transcript
from app.core.common.audio.fluency import analyze_fluency
from app.core.common.audio.preprocessing import align_timestamps, complement
from app.core.common.audio.timing import analyze_timing
from tests.audio.helpers import FakeClient, request, transcript


def prepared() -> PreparedAnswerAudio:
    speech = [Region(start_ms=1000, end_ms=7000), Region(start_ms=8000, end_ms=15000)]
    return PreparedAnswerAudio(
        answer_id="a",
        question_id="q",
        source_checksum="a" * 64,
        policy_fingerprint="test",
        status="ready",
        duration_ms=16000,
        speech=speech,
        non_speech=complement(speech, 16000),
        transcript=align_timestamps(transcript(), 16000),
        stage_statuses={},
    )


def test_internal_pause_is_not_leading_or_trailing_padding() -> None:
    result = analyze_timing(prepared(), AudioPolicy())
    assert result["silence"]["pause_count"] == 1
    assert result["silence"]["internal_non_speech_ms"] == 1000
    assert result["silence"]["leading_non_speech_ms"] == 1000
    assert result["silence"]["trailing_non_speech_ms"] == 1000
    assert result["speed"]["excluding_vad_non_speech"] > result["speed"]["including_internal_pauses"]
    assert len(result["stability"]["windows"]) == 2


def test_invalid_timing_is_flagged_not_fabricated() -> None:
    original = Transcript(
        text="가 나 다 라",
        model="fake",
        words=[
            TimedWord(text="가", start_ms=0, end_ms=100),
            TimedWord(text="나", start_ms=90, end_ms=200),
            TimedWord(text="다"),
            TimedWord(text="라", start_ms=300, end_ms=99999),
        ],
    )
    aligned = align_timestamps(original, 1000)
    assert [word.timing_status for word in aligned.words] == ["valid", "invalid", "missing", "invalid"]
    assert aligned.words[-1].end_ms == 99999
    assert original.words[0].timing_status == "missing"


def test_overlapping_vad_is_rejected() -> None:
    with pytest.raises(ValueError, match="timeline"):
        complement([Region(start_ms=0, end_ms=100), Region(start_ms=90, end_ms=200)], 1000)


def test_no_speech_has_no_zero_speed_score() -> None:
    data = prepared().model_copy(update={"speech": [], "non_speech": [Region(start_ms=0, end_ms=16000)]})
    result = analyze_timing(data, AudioPolicy())
    assert result["speed"] is None
    assert result["stability"] is None
    assert result["reason"] == "no_speech_detected"


@pytest.mark.asyncio
async def test_fabricated_fluency_evidence_is_rejected() -> None:
    client = FakeClient(
        {
            "observations": [{"kind": "filler", "start_char": 0, "end_char": 1, "quote": "어", "explanation": "test"}],
            "final_sentence": "complete",
            "final_sentence_evidence": "감사합니다",
            "comment": "test",
        }
    )
    with pytest.raises(AudioError, match="invalid_fluency_evidence"):
        await analyze_fluency(prepared(), request().answers[0], AudioPolicy(), client)


@pytest.mark.asyncio
async def test_forced_recording_end_cannot_be_classified_as_user_incompletion() -> None:
    client = FakeClient(
        {"observations": [], "final_sentence": "incomplete", "final_sentence_evidence": "감사합니다", "comment": "test"}
    )
    answer = request().answers[0].model_copy(update={"end_reason": "timeout"})
    result = await analyze_fluency(prepared(), answer, AudioPolicy(), client)
    assert result["final_sentence"] == "uncertain"
    assert result["acoustic_stuttering"]["status"] == "unavailable"
