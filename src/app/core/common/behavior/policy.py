"""Initial observation thresholds; these are not interview grading criteria."""

from decimal import ROUND_HALF_UP, Decimal

SCHEMA_VERSION = "behavior-v1"
MODEL_VERSION = "mp-face-pose-v1"
THRESHOLDS_VERSION = "behavior-policy-v1"
SAMPLE_MS = 200
MIN_DURATION_MS = 10_000
MIN_COVERAGE = 0.30
READY_COVERAGE = 0.80
READY_SAMPLE_COVERAGE = 0.90
MIN_EVENT_MS = 2_000
MAX_EVENTS = 60
MAX_DURATION_MS = 3_600_000
MAX_IMAGE_EDGE = 640
HEAD_ANGLE_DEGREES = 20.0
SHOULDER_ANGLE_DEGREES = 8.0
STABLE_SPEED = 0.15
SMILE_THRESHOLD = 0.5
KEYS = ("faceVisibleRatio", "headFacingRatio", "shoulderLevelRatio", "upperBodyStableRatio", "smileShapeRatio")
EVENT_METRICS = {
    "FACE_NOT_VISIBLE": KEYS[0],
    "HEAD_TURNED": KEYS[1],
    "SHOULDER_TILTED": KEYS[2],
    "UPPER_BODY_MOVEMENT": KEYS[3],
}
SUMMARY_TEMPLATES = (
    "얼굴이 관찰된 구간은 판단 가능한 시간의 {p}%입니다.",
    "머리가 카메라 정면 방향으로 추정된 구간은 방향을 판단할 수 있는 시간의 {p}%입니다.",
    "어깨선이 수평 범위에 있던 구간은 어깨를 관찰할 수 있는 시간의 {p}%입니다.",
    "상체 중심 이동이 기준 이하인 구간은 움직임을 판단할 수 있는 시간의 {p}%입니다.",
    "미소와 비슷한 입꼬리 형태가 관찰된 구간은 측정 가능 시간의 {p}%입니다.",
)


def fraction(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return float((Decimal(numerator) / Decimal(denominator)).quantize(Decimal(".000001"), rounding=ROUND_HALF_UP))


def percentage(numerator: int, denominator: int) -> str:
    value = Decimal(numerator) * 100 / Decimal(denominator)
    return str(value.quantize(Decimal(".1"), rounding=ROUND_HALF_UP))


def bin_width(duration_ms: int) -> int:
    return max(10_000, ((duration_ms + 59_999) // 60_000) * 1_000)
