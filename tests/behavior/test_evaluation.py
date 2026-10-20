from __future__ import annotations

from pathlib import Path

import pytest
from evaluation.behavior.evaluate import Clip, Label, compare
from pydantic import ValidationError

from app.core.common.behavior.policy import KEYS
from tests.behavior.test_cli import prepared


def test_evaluation_uses_overlap_time_and_keeps_missing_observation_visible() -> None:
    labels = [
        Label(start_ms=0, end_ms=1000, observed=True),
        Label(start_ms=1000, end_ms=2000, observed=False),
        Label(start_ms=2000, end_ms=3000, observed=None),
    ]
    counts = dict.fromkeys(
        ("tpMs", "fpMs", "fnMs", "tnMs", "unobservableMs", "correctlyExcludedMs", "missedObservationMs"), 0
    )
    compare(labels, 500, 1500, True, counts)
    compare(labels, 2000, 3000, None, counts)
    compare(labels, 0, 1000, None, counts)
    assert counts["tpMs"] == 500
    assert counts["fpMs"] == 500
    assert counts["correctlyExcludedMs"] == 1000
    assert counts["missedObservationMs"] == 1000


def test_evaluation_rejects_overlapping_human_labels(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="nonoverlapping"):
        Clip(
            clip_id="clip",
            person_code="p1",
            condition="frontal",
            prepared=prepared(tmp_path),
            annotations={
                key: [Label(start_ms=0, end_ms=2000, observed=True), Label(start_ms=1000, end_ms=3000, observed=False)]
                for key in KEYS
            },
        )
