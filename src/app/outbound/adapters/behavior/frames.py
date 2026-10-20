from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from fractions import Fraction
from typing import TYPE_CHECKING

from app.core.common.behavior.policy import MAX_IMAGE_EDGE, SAMPLE_MS
from app.core.common.video.dto import PreparedVideo

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray


@dataclass(frozen=True)
class FrameSample:
    slot: int
    timestamp_ms: int
    rgb: NDArray[np.uint8]


def _display_frame(rgb: NDArray[np.uint8], sar: Fraction, rotation: float) -> NDArray[np.uint8]:
    import numpy as np  # noqa: PLC0415 -- native dependencies are optional and loaded only during inference
    from PIL import Image  # noqa: PLC0415 -- native dependencies are optional and loaded only during inference

    image = Image.fromarray(rgb)
    display_width = max(1, round(image.width * sar))
    if display_width != image.width:
        image = image.resize((display_width, image.height), Image.Resampling.BILINEAR)
    if rotation % 360:
        # ffprobe's display-matrix angle is counter-clockwise in display coordinates.
        image = image.rotate(rotation, expand=True, resample=Image.Resampling.BILINEAR)
    factor = min(1.0, MAX_IMAGE_EDGE / max(image.size))
    if factor < 1:
        image = image.resize(
            (max(1, round(image.width * factor)), max(1, round(image.height * factor))), Image.Resampling.BILINEAR
        )
    return np.ascontiguousarray(image, dtype=np.uint8)


def samples(prepared: PreparedVideo) -> Iterator[FrameSample]:
    import av  # noqa: PLC0415 -- native dependencies are optional and loaded only during inference
    import numpy as np  # noqa: PLC0415 -- optional native dependency

    path = prepared.path
    with av.open(path, mode="r", options={"protocol_whitelist": "file", "enable_drefs": "0"}) as container:
        streams = [
            stream
            for stream in container.streams.video
            if not (stream.disposition & av.stream.Disposition.attached_pic)
        ]
        if len(streams) != 1 or streams[0].codec_context.name != prepared.codec:
            raise ValueError("validated video stream changed")
        stream = streams[0]
        stream.thread_type = "NONE"
        stream.codec_context.thread_count = 1
        aspect = stream.sample_aspect_ratio or Fraction(1)
        sar = Fraction(aspect.numerator, aspect.denominator)
        if sar <= 0:
            # Encoders may write an unspecified 0/1 SAR; the existing validator uses square pixels.
            sar = Fraction(1)
        origin: Fraction | None = None
        previous: Fraction | None = None
        last_slot = -1
        decoded_count = 0
        for frame in container.decode(stream):
            decoded_count += 1
            if frame.pts is None or frame.time_base is None:
                raise ValueError("missing presentation timestamp")
            pts = Fraction(frame.pts) * frame.time_base
            if previous is not None and pts <= previous:
                raise ValueError("presentation timestamps must increase")
            previous = pts
            if origin is None:
                origin = pts
            timestamp = int((pts - origin) * 1_000)
            if timestamp >= prepared.duration_ms:
                raise ValueError("frame exceeds validated duration")
            slot = timestamp // SAMPLE_MS
            if slot == last_slot:
                continue
            last_slot = slot
            rgb = _display_frame(
                np.asarray(frame.to_ndarray(format="rgb24"), dtype=np.uint8), sar, prepared.rotation_degrees
            )
            yield FrameSample(slot, timestamp, rgb)
        if decoded_count != prepared.frame_count:
            raise ValueError("validated frame count changed")
