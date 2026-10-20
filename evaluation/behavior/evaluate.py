"""Compare local 200ms observations with explicit human annotations; no paid services."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, model_validator

from app.core.common.behavior.aggregate import BehaviorAggregator
from app.core.common.behavior.dto import BehaviorModel
from app.core.common.behavior.observations import ObservationFilter
from app.core.common.behavior.policy import KEYS, SAMPLE_MS
from app.core.common.video.dto import PreparedVideo
from app.outbound.adapters.behavior.cli import _verify_source, native_output_to_stderr
from app.outbound.adapters.behavior.frames import samples
from app.outbound.adapters.behavior.manifest import load_manifest
from app.outbound.adapters.behavior.mediapipe_models import MediaPipeModels


class Label(BehaviorModel):
    start_ms: Annotated[int, Field(ge=0)]
    end_ms: Annotated[int, Field(gt=0)]
    observed: bool | None


class Clip(BehaviorModel):
    clip_id: Annotated[str, Field(min_length=1, max_length=100)]
    person_code: Annotated[str, Field(min_length=1, max_length=100)]
    condition: Annotated[str, Field(min_length=1, max_length=100)]
    prepared: PreparedVideo
    annotations: dict[str, list[Label]]

    @model_validator(mode="after")
    def valid_ranges(self) -> Clip:
        if set(self.annotations) != set(KEYS):
            raise ValueError("annotate all five metrics explicitly")
        for labels in self.annotations.values():
            end = 0
            for label in labels:
                if not end <= label.start_ms < label.end_ms <= self.prepared.duration_ms:
                    raise ValueError("labels must be ordered, nonoverlapping and inside the clip")
                end = label.end_ms
        return self


class Dataset(BehaviorModel):
    clips: Annotated[list[Clip], Field(min_length=1)]


def compare(
    labels: Sequence[Label],
    start: int,
    end: int,
    predicted: bool | None,
    counts: dict[str, int],
) -> None:
    for label in labels:
        weight = max(0, min(end, label.end_ms) - max(start, label.start_ms))
        if not weight:
            continue
        if label.observed is None:
            counts["unobservableMs"] += weight
            if predicted is None:
                counts["correctlyExcludedMs"] += weight
        elif predicted is None:
            counts["missedObservationMs"] += weight
        elif label.observed and predicted:
            counts["tpMs"] += weight
        elif predicted:
            counts["fpMs"] += weight
        elif label.observed:
            counts["fnMs"] += weight
        else:
            counts["tnMs"] += weight


def evaluate(clip: Clip, model_path: Path) -> dict[str, Any]:
    manifest = load_manifest(model_path, "mp-face-pose-v1")
    _verify_source(clip.prepared)
    collector = BehaviorAggregator(clip.prepared.video_id, clip.prepared.duration_ms)
    filtered = ObservationFilter()
    names = ("tpMs", "fpMs", "fnMs", "tnMs", "unobservableMs", "correctlyExcludedMs", "missedObservationMs")
    counts = {key: dict.fromkeys(names, 0) for key in KEYS}
    models = MediaPipeModels(manifest, model_path)
    try:
        for frame in samples(clip.prepared):
            observation = models.observe(frame)
            collector.add(observation)
            for key, predicted in filtered.flags(observation).items():
                start = frame.slot * SAMPLE_MS
                compare(
                    clip.annotations[key],
                    start,
                    min(start + SAMPLE_MS, clip.prepared.duration_ms),
                    predicted,
                    counts[key],
                )
    finally:
        models.close()
    # Labels in missing frame slots are not evaluated as successful observations.
    metrics = {}
    for key, values in counts.items():
        known = sum(label.end_ms - label.start_ms for label in clip.annotations[key] if label.observed is not None)
        unobservable = sum(label.end_ms - label.start_ms for label in clip.annotations[key] if label.observed is None)
        measured = sum(values[name] for name in ("tpMs", "fpMs", "fnMs", "tnMs"))
        denominator = 2 * values["tpMs"] + values["fpMs"] + values["fnMs"]
        predicted_positive = values["tpMs"] + values["fpMs"]
        true_positive = sum(label.end_ms - label.start_ms for label in clip.annotations[key] if label.observed is True)
        metrics[key] = {
            **values,
            "f1": 2 * values["tpMs"] / denominator if denominator else None,
            "annotationCoverage": measured / known if known else None,
            "exclusionRecall": (unobservable - values["unobservableMs"] + values["correctlyExcludedMs"]) / unobservable
            if unobservable
            else None,
            "absoluteRatioError": abs(predicted_positive / measured - true_positive / known)
            if measured and known
            else None,
        }
    outcome = collector.finish()
    return {
        "clipId": clip.clip_id,
        "personCode": clip.person_code,
        "condition": clip.condition,
        "status": outcome.status,
        "metrics": metrics,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--model-manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    dataset = Dataset.model_validate_json(args.dataset.read_bytes())
    with native_output_to_stderr():
        reports = [evaluate(clip, args.model_manifest.resolve()) for clip in dataset.clips]
    report = {"modelVersion": "mp-face-pose-v1", "thresholdsVersion": "behavior-policy-v1", "clips": reports}
    args.out.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n")
    rows = [
        "# 행동 관찰 평가",
        "",
        "| clip | 조건 | 지표 | F1 | 관찰 범위 | 비율 절대오차 |",
        "|---|---|---|---|---|---|",
    ]
    for clip in reports:
        for key, metric in clip["metrics"].items():
            rows.append(
                f"| {clip['clipId']} | {clip['condition']} | {key} | {metric['f1']} | "
                f"{metric['annotationCoverage']} | {metric['absoluteRatioError']} |"
            )
    args.out.with_suffix(".md").write_text("\n".join(rows) + "\n")


if __name__ == "__main__":
    main()
