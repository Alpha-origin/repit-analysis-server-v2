from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

MATRIX_SIZE = 4
ROTATION_SIZE = 3
SINGULAR_EPSILON = 1e-6
MIN_SHOULDER_FRACTION = 0.10


def head_angles(matrix: NDArray[np.float64]) -> tuple[float, float] | None:
    import numpy as np  # noqa: PLC0415 -- native dependencies are optional and loaded only during inference

    if matrix.shape != (MATRIX_SIZE, MATRIX_SIZE) or not np.isfinite(matrix).all():
        return None
    try:
        u, scales, vt = np.linalg.svd(matrix[:ROTATION_SIZE, :ROTATION_SIZE])
    except np.linalg.LinAlgError:
        return None
    if min(scales) <= SINGULAR_EPSILON:
        return None
    rotation = u @ vt
    if np.linalg.det(rotation) < 0:
        u[:, -1] *= -1
        rotation = u @ vt
    horizontal = math.hypot(rotation[0, 0], rotation[1, 0])
    if horizontal < SINGULAR_EPSILON:
        return None
    yaw = math.degrees(math.atan2(-rotation[2, 0], horizontal))
    pitch = math.degrees(math.atan2(rotation[2, 1], rotation[2, 2]))
    return yaw, pitch


def shoulder_geometry(
    left: tuple[float, float],
    right: tuple[float, float],
    image_width: int,
) -> tuple[float, tuple[float, float], float] | None:
    if not all(math.isfinite(value) for value in (*left, *right)):
        return None
    width = math.dist(left, right)
    if width < image_width * MIN_SHOULDER_FRACTION:
        return None
    tilt = math.degrees(math.atan2(abs(right[1] - left[1]), abs(right[0] - left[0])))
    return tilt, ((left[0] + right[0]) / 2, (left[1] + right[1]) / 2), width
