from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.common.behavior.dto import BehaviorData
from app.core.common.behavior.validation import quality

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("name", "status"),
    [
        ("behavior-ready.json", "ready"),
        ("behavior-partial.json", "partial"),
        ("behavior-zero-face.json", "partial"),
    ],
)
def test_shared_contract_fixtures(name: str, status: str) -> None:
    data = BehaviorData.model_validate_json((FIXTURES / name).read_bytes())
    assert quality(data) == status
    assert data.wire() == json.loads((FIXTURES / name).read_text())


@pytest.mark.parametrize("mutation", ["value", "bin", "event", "null", "extra", "status", "warning", "duration"])
def test_reject_incoherent_success_documents(mutation: str) -> None:
    raw = json.loads((FIXTURES / "behavior-ready.json").read_text())
    if mutation == "value":
        raw["metrics"]["headFacingRatio"]["value"] = 0.99
    elif mutation == "bin":
        raw["timeline"][0]["metrics"]["faceVisibleRatio"]["numeratorMs"] += 200
    elif mutation == "event":
        raw["events"][0]["endMs"] += 1000
    elif mutation == "null":
        raw["metrics"]["faceVisibleRatio"]["value"] = None
    elif mutation == "extra":
        raw["image"] = "forbidden"
    elif mutation == "status":
        raw["metrics"]["faceVisibleRatio"].update(status="UNAVAILABLE", value=None, unavailableReason="LOW_COVERAGE")
    elif mutation == "warning":
        raw["warnings"] = ["LOW_POSE_COVERAGE"]
    else:
        raw["durationMs"] = 4000
    with pytest.raises(ValidationError):
        BehaviorData.model_validate(raw)


def test_strict_json_types_and_finite_numbers() -> None:
    raw = json.loads((FIXTURES / "behavior-ready.json").read_text())
    for bad in (True, "60000", 60000.5):
        changed = deepcopy(raw)
        changed["durationMs"] = bad
        with pytest.raises(ValidationError):
            BehaviorData.model_validate(changed)
    raw["metrics"]["faceVisibleRatio"]["value"] = float("nan")
    with pytest.raises(ValidationError):
        BehaviorData.model_validate(raw)


def test_unavailable_reasons_follow_observation_evidence() -> None:
    raw = json.loads((FIXTURES / "behavior-partial.json").read_text())
    raw["metrics"]["shoulderLevelRatio"]["unavailableReason"] = "NO_SINGLE_FACE"
    with pytest.raises(ValidationError, match="reason mismatch"):
        BehaviorData.model_validate(raw)
