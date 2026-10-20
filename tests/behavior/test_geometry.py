from __future__ import annotations

import math

import pytest

from app.outbound.adapters.behavior.geometry import head_angles, shoulder_geometry

np = pytest.importorskip("numpy")


@pytest.mark.parametrize("degrees", [-30, 0, 30])
def test_yaw_and_scale(degrees: int) -> None:
    angle = math.radians(degrees)
    matrix = np.eye(4)
    matrix[:3, :3] = [[math.cos(angle), 0, math.sin(angle)], [0, 1, 0], [-math.sin(angle), 0, math.cos(angle)]]
    matrix[:3, :3] *= 4
    result = head_angles(matrix)
    assert result is not None
    assert result == pytest.approx((degrees, 0))


def test_pitch_singular_and_nonfinite_matrices() -> None:
    angle = math.radians(25)
    matrix = np.eye(4)
    matrix[:3, :3] = [[1, 0, 0], [0, math.cos(angle), -math.sin(angle)], [0, math.sin(angle), math.cos(angle)]]
    assert head_angles(matrix) == pytest.approx((0, 25))
    assert head_angles(np.zeros((4, 4))) is None
    assert head_angles(np.full((4, 4), float("nan"))) is None
    assert head_angles(np.eye(3)) is None


def test_shoulders_are_pixel_based_and_mirror_invariant() -> None:
    result = shoulder_geometry((100, 100), (300, 120), 640)
    mirrored = shoulder_geometry((540, 100), (340, 120), 640)
    assert result is not None
    assert mirrored is not None
    assert result[0] == mirrored[0]
    assert result[1] == pytest.approx((200.0, 110.0))
    assert shoulder_geometry((100, 100), (101, 100), 640) is None
