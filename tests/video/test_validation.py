from __future__ import annotations

from collections.abc import Sequence
from fractions import Fraction
from pathlib import Path

import pytest

from app.core.common.video.errors import VideoStageError
from app.core.common.video.media_validation import TimelineAccumulator
from app.core.common.video.policy import VideoLimits
from app.outbound.adapters.video.media_backend import LocalVideoMediaBackend
from tests.video.helpers import security
from tests.video.media_fixtures import corrupt_tail, lavfi_video, piped_webm, require_media_tools

LIMITS = VideoLimits()


def timeline(
    pts: Sequence[int | None],
    time_base: Fraction,
    durations: list[int | None] | None = None,
    limits: VideoLimits = LIMITS,
) -> TimelineAccumulator:
    accumulator = TimelineAccumulator(time_base, limits, 640, 480, Fraction(1), 0.0)
    for index, stamp in enumerate(pts):
        accumulator.add(stamp, (durations or [None] * len(pts))[index], 640, 480)
    return accumulator


def code_of(function: object) -> str:
    with pytest.raises(VideoStageError) as caught:
        function()  # type: ignore[operator]
    return caught.value.code


def test_vfr_quantization_and_duration_boundaries() -> None:
    ms = Fraction(1, 1000)
    # 60 fps quantized to a 1 ms container clock: gaps alternate 16/17 ms -> accepted.
    sixty = [round(index * 1000 / 60) for index in range(600)]
    assert timeline(sixty, ms).finish(None).frame_count == 600
    ntsc = [round(index * 1001 / 60) for index in range(600)]  # 59.94
    assert timeline(ntsc, ms).finish(None).average_fps < 60
    exact = timeline(list(range(120)), Fraction(1, 60)).finish(None)  # exact 60 fps clock
    assert exact.duration_ms == 2000
    vfr = [0, 16, 33, 50, 100, 300, 317, 334, 1000]
    assert timeline(vfr, ms).finish(None).frame_count == 9
    # Real Chrome MediaRecorder jitter: occasional 2-4 ms gaps at ~20 fps must be accepted.
    browser = [0, 2, 19, 83, 176, 185, 251, 319, 385, 386, 452, 518, 585, 652, 719, 786, 852, 919, 985, 1051]
    assert timeline(browser, ms).finish(None).frame_count == len(browser)
    # 61 frames inside one second is over the limit even when the rest of the file is slow.
    burst = [index * 10 for index in range(61)] + [5000 + index * 100 for index in range(100)]
    assert code_of(lambda: timeline(burst, ms)) == "VIDEO_LIMIT_EXCEEDED"
    # Sustained 61 fps passes the per-gap tick tolerance but fails the overall rate.
    sixty_one = [round(index * 1000 / 61) for index in range(610)]
    assert code_of(lambda: timeline(sixty_one, ms).finish(None)) == "VIDEO_LIMIT_EXCEEDED"
    hundred_twenty = [round(index * 1000 / 120) for index in range(240)]
    assert code_of(lambda: timeline(hundred_twenty, ms)) == "VIDEO_LIMIT_EXCEEDED"
    # Exactly 3600 s (Fraction comparison, not rounded ms) is allowed; one more tick is not.
    hour = timeline(list(range(3600)), Fraction(1)).finish(None)
    assert hour.duration_ms == 3_600_000
    over = timeline(list(range(3600)), Fraction(1), durations=[None] * 3599 + [2])
    assert code_of(lambda: over.finish(None)) == "VIDEO_LIMIT_EXCEEDED"
    just_over = timeline([0, 3_599_999], ms, durations=[None, 2])
    assert code_of(lambda: just_over.finish(None)) == "VIDEO_LIMIT_EXCEEDED"
    # A wrongly short header cannot shorten the governing duration; a longer declared one governs.
    assert timeline([0, 1000], ms, [1000, 1000]).finish(Fraction(1)).duration_ms == 2000
    declared_long = timeline([0, 1000], ms, [1000, 1000])
    assert code_of(lambda: declared_long.finish(Fraction(3601))) == "VIDEO_LIMIT_EXCEEDED"
    # Missing header duration is recovered from the decoded timeline.
    assert timeline([0, 100, 200], ms).finish(None).duration_ms == 300


def test_invalid_pts_and_metadata_fail_closed() -> None:
    ms = Fraction(1, 1000)
    assert code_of(lambda: timeline([0, None, 40], ms)) == "VIDEO_DECODE_FAILED"
    assert code_of(lambda: timeline([0, 40, 40], ms)) == "VIDEO_DECODE_FAILED"
    assert code_of(lambda: timeline([0, 40, 20], ms)) == "VIDEO_DECODE_FAILED"
    assert code_of(lambda: timeline([], ms).finish(None)) == "VIDEO_DECODE_FAILED"
    assert code_of(lambda: timeline([5], ms).finish(None)) == "VIDEO_DECODE_FAILED"  # single frame, no duration
    assert timeline([5], ms, [40]).finish(None).duration_ms == 40
    assert timeline([5], ms).finish(Fraction(1, 2)).duration_ms == 500
    accumulator = TimelineAccumulator(ms, LIMITS, 640, 480, Fraction(1), 0.0)
    accumulator.add(0, 33, 640, 480)
    with pytest.raises(VideoStageError) as caught:
        accumulator.add(33, 33, 2560, 1440)  # dynamic resolution change beyond limits
    assert caught.value.code == "VIDEO_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_full_decode_catches_late_corruption_and_limit_bypass(tmp_path: Path) -> None:
    require_media_tools()
    backend = LocalVideoMediaBackend(security())
    for codec, suffix in (("libvpx-vp9", ".webm"), ("libx264", ".mp4")):
        clean = lavfi_video(tmp_path / f"clean{suffix}", codec=codec, seconds=3)
        damaged = corrupt_tail(clean, tmp_path / f"damaged{suffix}", keep_ratio=0.7)
        inspected = await backend.inspect(str(damaged), LIMITS)  # header and metadata still look fine
        with pytest.raises(VideoStageError) as caught:
            await backend.validate(str(damaged), inspected, LIMITS)
        assert caught.value.code == "VIDEO_DECODE_FAILED"
    # A stream with no header duration (live/piped WebM) is measured by decoding the whole file.
    piped = piped_webm(tmp_path / "piped.webm", frames=30)
    short_limit = VideoLimits(max_duration_ms=2_000)
    inspected = await backend.inspect(str(piped), short_limit)
    assert inspected["declaredDuration"] is None
    with pytest.raises(VideoStageError) as caught:
        await backend.validate(str(piped), inspected, short_limit)
    assert caught.value.code == "VIDEO_LIMIT_EXCEEDED"
    assert (await backend.validate(str(piped), inspected, LIMITS))["durationMs"] == 3000
