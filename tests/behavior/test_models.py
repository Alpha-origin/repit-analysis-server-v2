from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.outbound.adapters.behavior.frames import FrameSample
from app.outbound.adapters.behavior.mediapipe_models import _observation

np = pytest.importorskip("numpy")


def face(count: int = 1) -> SimpleNamespace:
    points = [SimpleNamespace(x=0.25, y=0.2, z=0), SimpleNamespace(x=0.75, y=0.8, z=0)]
    shapes = [SimpleNamespace(category_name=name, score=0.8) for name in ("mouthSmileLeft", "mouthSmileRight")]
    return SimpleNamespace(
        face_landmarks=[points] * count,
        face_blendshapes=[shapes] * count,
        facial_transformation_matrixes=[np.eye(4)] * count,
    )


def pose(count: int = 1) -> SimpleNamespace:
    points = [SimpleNamespace(x=0.5, y=0.5, visibility=1.0, presence=1.0) for _ in range(33)]
    points[11].x = 0.25
    points[12].x = 0.75
    return SimpleNamespace(pose_landmarks=[points] * count)


def test_multiple_faces_never_select_the_first_person() -> None:
    observation = _observation(FrameSample(0, 0, np.zeros((480, 640, 3), dtype=np.uint8)), face(2), pose(), False)
    assert observation.face_count == 2
    assert observation.yaw is None
    assert observation.tilt is None
    assert observation.smile is None


def test_missing_shoulder_presence_excludes_only_posture() -> None:
    result = pose()
    result.pose_landmarks[0][11].presence = None
    observation = _observation(FrameSample(0, 0, np.zeros((480, 640, 3), dtype=np.uint8)), face(), result, False)
    assert observation.face_count == 1
    assert observation.yaw == 0
    assert observation.smile == 0.8
    assert observation.tilt is None


def test_malformed_face_landmarks_cannot_be_successful_presence() -> None:
    result = face()
    result.face_landmarks[0][0].x = float("nan")
    observation = _observation(FrameSample(0, 0, np.zeros((480, 640, 3), dtype=np.uint8)), result, pose(), False)
    assert observation.face_count is None
    assert observation.tilt is None


def test_face_only_and_missing_component_are_preserved() -> None:
    observation = _observation(FrameSample(0, 0, np.zeros((480, 640, 3), dtype=np.uint8)), face(), None, True)
    assert observation.component_failure is True
    assert observation.face_count == 1
    assert observation.pose_count is None
    assert observation.yaw == 0
