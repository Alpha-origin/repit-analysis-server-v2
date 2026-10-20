from __future__ import annotations

import math
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.core.common.behavior.observations import Observation
from app.outbound.adapters.behavior.geometry import head_angles, shoulder_geometry
from app.outbound.adapters.behavior.manifest import ModelManifest

if TYPE_CHECKING:
    from app.outbound.adapters.behavior.frames import FrameSample

LEFT_SHOULDER = 11
RIGHT_SHOULDER = 12
MIN_VISIBILITY = 0.5
MIN_FACE_HEIGHT = 0.12
MIN_FACE_AREA = 0.01


class MediaPipeModels:
    def __init__(self, manifest: ModelManifest, path: Path) -> None:
        import mediapipe as mp  # noqa: PLC0415 -- native dependencies are optional and loaded only during inference

        self._mp = mp
        self._stack = ExitStack()
        vision = mp.tasks.vision
        base = mp.tasks.BaseOptions
        try:
            self._face = self._stack.enter_context(
                vision.FaceLandmarker.create_from_options(
                    vision.FaceLandmarkerOptions(
                        base_options=base(
                            model_asset_path=str(manifest.asset_path(path, manifest.face)), delegate=base.Delegate.CPU
                        ),
                        running_mode=vision.RunningMode.VIDEO,
                        num_faces=2,
                        min_face_detection_confidence=0.5,
                        min_face_presence_confidence=0.5,
                        min_tracking_confidence=0.5,
                        output_face_blendshapes=True,
                        output_facial_transformation_matrixes=True,
                    )
                )
            )
            self._pose = self._stack.enter_context(
                vision.PoseLandmarker.create_from_options(
                    vision.PoseLandmarkerOptions(
                        base_options=base(
                            model_asset_path=str(manifest.asset_path(path, manifest.pose)), delegate=base.Delegate.CPU
                        ),
                        running_mode=vision.RunningMode.VIDEO,
                        num_poses=2,
                        min_pose_detection_confidence=0.5,
                        min_pose_presence_confidence=0.5,
                        min_tracking_confidence=0.5,
                        output_segmentation_masks=False,
                    )
                )
            )
        except BaseException:
            self._stack.close()
            raise
        self._face_failed = self._pose_failed = False

    def close(self) -> None:
        self._stack.close()

    def observe(self, sample: FrameSample) -> Observation:
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=sample.rgb)
        face = pose = None
        if not self._face_failed:
            try:
                face = self._face.detect_for_video(image, sample.timestamp_ms)
            except (ValueError, RuntimeError):
                self._face_failed = True
        if not self._pose_failed:
            try:
                pose = self._pose.detect_for_video(image, sample.timestamp_ms)
            except (ValueError, RuntimeError):
                self._pose_failed = True
        return _observation(sample, face, pose, self._face_failed or self._pose_failed)


def _face_features(face: Any) -> tuple[int | None, float | None, float | None, float | None, bool]:
    count = len(face.face_landmarks) if face is not None else None
    if count != 1:
        return count, None, None, None, False
    points = face.face_landmarks[0]
    if not points or not all(math.isfinite(v) for point in points for v in (point.x, point.y, point.z)):
        return None, None, None, None, False
    xs, ys = [p.x for p in points], [p.y for p in points]
    if max(xs) < 0 or min(xs) > 1 or max(ys) < 0 or min(ys) > 1:
        return None, None, None, None, False
    box_width, box_height = max(xs) - min(xs), max(ys) - min(ys)
    small = box_height < MIN_FACE_HEIGHT or box_width * box_height < MIN_FACE_AREA
    angles = None
    if not small and len(face.facial_transformation_matrixes) == 1:
        angles = head_angles(face.facial_transformation_matrixes[0])
    yaw, pitch = angles if angles is not None else (None, None)
    return count, yaw, pitch, _smile(face), small


def _smile(face: Any) -> float | None:
    if len(face.face_blendshapes) != 1:
        return None
    scores = {item.category_name: item.score for item in face.face_blendshapes[0]}
    left, right = scores.get("mouthSmileLeft"), scores.get("mouthSmileRight")
    if left is None or right is None or not all(math.isfinite(v) and 0 <= v <= 1 for v in (left, right)):
        return None
    return float((left + right) / 2)


def _observation(sample: FrameSample, face: Any, pose: Any, failure: bool) -> Observation:
    face_count, yaw, pitch, smile, small = _face_features(face)
    pose_count = len(pose.pose_landmarks) if pose is not None else None
    tilt: float | None = None
    width: float | None = None
    center: tuple[float, float] | None = None
    if face_count == 1 and pose_count == 1 and len(pose.pose_landmarks[0]) > RIGHT_SHOULDER:
        shoulders = [pose.pose_landmarks[0][i] for i in (LEFT_SHOULDER, RIGHT_SHOULDER)]
        if all(_visible(point) for point in shoulders):
            height, image_width = sample.rgb.shape[:2]
            left_point, right_point = [(float(p.x * image_width), float(p.y * height)) for p in shoulders]
            geometry = shoulder_geometry(left_point, right_point, image_width)
            if geometry is not None:
                tilt, center, width = geometry
    return Observation(
        sample.slot, sample.timestamp_ms, face_count, pose_count, yaw, pitch, tilt, center, width, smile, small, failure
    )


def _visible(point: Any) -> bool:
    values = (point.x, point.y, point.visibility, point.presence)
    return all(value is not None and math.isfinite(value) and 0 <= value <= 1 for value in values) and (
        point.visibility >= MIN_VISIBILITY and point.presence >= MIN_VISIBILITY
    )
