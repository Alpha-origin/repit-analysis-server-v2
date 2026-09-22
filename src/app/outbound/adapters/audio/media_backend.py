from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import json
import math
import shutil
import sys
import uuid
import wave
from array import array
from pathlib import Path
from typing import Any

from app.core.common.audio.dto import AudioAnswer, AudioError, AudioPolicy, Region, TimedWord, Transcript
from app.core.common.audio.preprocessing import align_timestamps

PCM_WIDTH = 2
SAMPLE_RATE = 16000
CLIPPING_LEVEL = 32760

FORMATS = "wav,mp3,matroska,webm,mov,ogg,flac,aac"


def inside(root: Path, key: str) -> Path:
    candidate = (root / key).resolve()
    if Path(key).is_absolute() or not candidate.is_relative_to(root.resolve()) or candidate == root.resolve():
        raise AudioError("invalid_asset_key")
    return candidate


def file_sha256(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def package_version(name: str) -> str:
    return importlib.metadata.version(name)


class LocalAudioBackend:
    """Server-owned upload root; no arbitrary URL or filesystem access from API input."""

    def __init__(self, source_root: Path, artifact_root: Path) -> None:
        self.source_root = source_root.resolve()
        self.artifact_root = artifact_root.resolve()
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self._vad: Any = None
        self._whisper: dict[str, Any] = {}

    async def execute(  # noqa: PLR0911 — one implementation per preprocessing stage
        self, stage: str, answer: AudioAnswer, policy: AudioPolicy, inputs: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        if stage == "source":
            return await asyncio.to_thread(self._source, answer, policy)
        if stage == "inspect":
            return await self._inspect(Path(inputs["source"]["path"]), policy)
        if stage == "normalize":
            return await self._normalize(answer, policy, inputs)
        if stage == "quality":
            return await asyncio.to_thread(self._quality, Path(inputs["normalize"]["path"]))
        if stage == "vad":
            return await asyncio.to_thread(self._detect, Path(inputs["normalize"]["path"]), policy)
        if stage == "transcribe":
            return await asyncio.to_thread(self._transcribe, Path(inputs["normalize"]["path"]), policy)
        if stage == "align":
            transcript = Transcript.model_validate(inputs["transcribe"])
            aligned = align_timestamps(transcript, inputs["normalize"]["duration_ms"])
            return {**aligned.model_dump(), "implementation": "validated-asr-timestamps-v1"}
        raise AudioError("unknown_preprocessing_stage")

    def _source(self, answer: AudioAnswer, policy: AudioPolicy) -> dict[str, Any]:
        source = inside(self.source_root, answer.asset_key)
        if not source.is_file():
            raise AudioError("source_not_found")
        if source.stat().st_size > policy.max_bytes:
            raise AudioError("source_too_large")
        target = self.artifact_root / f"{answer.sha256}.source"
        temporary = target.with_suffix(f".{uuid.uuid4().hex}.tmp")
        checksum = hashlib.sha256()
        size = 0
        try:
            with source.open("rb") as incoming, temporary.open("xb") as outgoing:
                for block in iter(lambda: incoming.read(1024 * 1024), b""):
                    size += len(block)
                    if size > policy.max_bytes:
                        raise AudioError("source_too_large")
                    checksum.update(block)
                    outgoing.write(block)
            if not size or checksum.hexdigest() != answer.sha256:
                raise AudioError("source_checksum_mismatch")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return {"path": str(target), "sha256": answer.sha256, "bytes": size, "implementation": "sha256-copy-v1"}

    @staticmethod
    async def _command(executable: str, arguments: list[str], timeout_seconds: int) -> bytes:
        binary = shutil.which(executable)
        if binary is None:
            raise AudioError(f"missing_{executable}")
        process = await asyncio.create_subprocess_exec(
            binary,
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            async with asyncio.timeout(timeout_seconds):
                stdout, _ = await process.communicate()
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise
        if process.returncode != 0:
            raise AudioError(f"{executable}_failed")
        return stdout

    async def _inspect(self, path: Path, policy: AudioPolicy) -> dict[str, Any]:
        stdout = await self._command(
            "ffprobe",
            [
                "-v",
                "error",
                "-protocol_whitelist",
                "file",
                "-format_whitelist",
                FORMATS,
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ],
            policy.media_timeout_seconds,
        )
        data = json.loads(stdout)
        streams = [stream for stream in data["streams"] if stream["codec_type"] == "audio"]
        if len(streams) != 1:
            raise AudioError("ambiguous_or_missing_audio_stream")
        stream = streams[0]
        raw_duration = stream.get("duration", data.get("format", {}).get("duration"))
        duration = None if raw_duration in (None, "N/A") else round(float(raw_duration) * 1000)
        if duration is not None and (duration <= 0 or duration > policy.max_duration_ms):
            raise AudioError("duration_out_of_range")
        channels = int(stream["channels"])
        if channels not in (1, 2):
            raise AudioError("unsupported_channel_layout")
        version = (await self._command("ffprobe", ["-version"], policy.media_timeout_seconds)).decode().splitlines()[0]
        return {
            "stream_index": stream["index"],
            "codec": stream["codec_name"],
            "sample_rate": int(stream["sample_rate"]),
            "channels": channels,
            "declared_duration_ms": duration,
            "implementation": version,
        }

    async def _normalize(
        self, answer: AudioAnswer, policy: AudioPolicy, inputs: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        target = self.artifact_root / f"{answer.sha256}-{policy.fingerprint()}.wav"
        temporary = target.with_suffix(f".{uuid.uuid4().hex}.wav")
        try:
            await self._command(
                "ffmpeg",
                [
                    "-nostdin",
                    "-v",
                    "error",
                    "-xerror",
                    "-protocol_whitelist",
                    "file",
                    "-format_whitelist",
                    FORMATS,
                    "-i",
                    inputs["source"]["path"],
                    "-map",
                    f"0:{inputs['inspect']['stream_index']}",
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    str(policy.sample_rate),
                    "-c:a",
                    "pcm_s16le",
                    "-t",
                    str(policy.max_duration_ms / 1000 + 1),
                    "-f",
                    "wav",
                    str(temporary),
                ],
                policy.media_timeout_seconds,
            )
            metadata = await asyncio.to_thread(self._wave_metadata, temporary)
            if not 0 < metadata["duration_ms"] <= policy.max_duration_ms:
                raise AudioError("decoded_duration_out_of_range")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        version = (await self._command("ffmpeg", ["-version"], policy.media_timeout_seconds)).decode().splitlines()[0]
        return {
            **metadata,
            "path": str(target),
            "implementation": version,
            "channel_policy": "mono_passthrough_or_stereo_downmix",
            "time_basis": "decoded_audio_start_zero_no_trim",
            "source_sha256": answer.sha256,
        }

    @staticmethod
    def _wave_metadata(path: Path) -> dict[str, int]:
        with wave.open(str(path), "rb") as stream:
            if stream.getsampwidth() != PCM_WIDTH or stream.getnchannels() != 1 or stream.getframerate() != SAMPLE_RATE:
                raise AudioError("invalid_normalized_audio")
            count = stream.getnframes()
            return {"sample_count": count, "sample_rate": 16000, "duration_ms": round(count * 1000 / 16000)}

    @staticmethod
    def _quality(path: Path) -> dict[str, Any]:
        frames = []
        clipped = 0
        samples = 0
        peak = 0.0
        with wave.open(str(path), "rb") as stream:
            while raw := stream.readframes(320):
                values = array("h", raw)
                if sys.byteorder != "little":
                    values.byteswap()
                rms = math.sqrt(sum((value / 32768) ** 2 for value in values) / len(values))
                peak = max(peak, *(abs(value) / 32768 for value in values))
                clipped += sum(abs(value) >= CLIPPING_LEVEL for value in values)
                frames.append({"start_ms": round(samples / 16), "rms": rms, "all_zero": not any(values)})
                samples += len(values)
        if not samples:
            raise AudioError("empty_waveform")
        return {
            "peak": peak,
            "clipping_suspect_ratio": clipped / samples,
            "frames": frames,
            "frame_ms": 20,
            "implementation": "pcm-quality-v1",
            "limitations": ["Zero/low-energy frames are not confirmed recording failures."],
        }

    def _detect(self, path: Path, policy: AudioPolicy) -> dict[str, Any]:
        try:
            from silero_vad import (  # noqa: PLC0415 — optional worker dependency
                get_speech_timestamps,
                load_silero_vad,
                read_audio,
            )
        except ImportError as exc:
            raise AudioError("audio_dependencies_missing") from exc
        if self._vad is None:
            self._vad = load_silero_vad(onnx=True)
        audio = read_audio(str(path), sampling_rate=policy.sample_rate)
        regions = get_speech_timestamps(
            audio,
            self._vad,
            sampling_rate=policy.sample_rate,
            threshold=policy.vad_threshold,
            min_speech_duration_ms=policy.vad_min_speech_ms,
            min_silence_duration_ms=policy.vad_min_silence_ms,
            speech_pad_ms=0,
        )
        speech = [
            Region(start_ms=round(item["start"] / 16), end_ms=round(item["end"] / 16)).model_dump()
            for item in regions
            if round(item["end"] / 16) > round(item["start"] / 16)
        ]
        return {"speech": speech, "implementation": f"silero-vad/{package_version('silero-vad')}"}

    def _transcribe(self, path: Path, policy: AudioPolicy) -> dict[str, Any]:
        try:
            from faster_whisper import WhisperModel  # noqa: PLC0415 — optional worker dependency
        except ImportError as exc:
            raise AudioError("audio_dependencies_missing") from exc
        key = policy.fingerprint()
        if key not in self._whisper:
            self._whisper.clear()
            self._whisper[key] = WhisperModel(
                policy.whisper_model,
                device=policy.whisper_device,
                compute_type=policy.whisper_compute_type,
                local_files_only=policy.whisper_local_only,
                num_workers=1,
            )
        segments, _ = self._whisper[key].transcribe(
            str(path),
            language=policy.language,
            word_timestamps=True,
            vad_filter=False,
            condition_on_previous_text=False,
        )
        texts = []
        words: list[TimedWord] = []
        raw_segments = []
        for segment in segments:
            texts.append(segment.text)
            raw_segments.append(
                {
                    "text": segment.text,
                    "start_ms": round(segment.start * 1000),
                    "end_ms": round(segment.end * 1000),
                }
            )
            words.extend(
                TimedWord(text=word.word, start_ms=round(word.start * 1000), end_ms=round(word.end * 1000))
                for word in segment.words or []
            )
        transcript = Transcript(text="".join(texts), words=words, segments=raw_segments, model=policy.whisper_model)
        return {**transcript.model_dump(), "implementation": f"faster-whisper/{package_version('faster-whisper')}"}
