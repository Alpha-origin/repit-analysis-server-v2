from collections.abc import Sequence
from itertools import pairwise

from app.core.common.interview_qa.pdf_recovery_dto import Region


def area(box: Region) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def intersection(left: Region, right: Region) -> Region:
    return max(left[0], right[0]), max(left[1], right[1]), min(left[2], right[2]), min(left[3], right[3])


def union_area(boxes: Sequence[Region]) -> float:
    usable = [box for box in boxes if area(box) > 0]
    xs = sorted({point for box in usable for point in (box[0], box[2])})
    total = 0.0
    for left, right in pairwise(xs):
        intervals = sorted((box[1], box[3]) for box in usable if box[0] < right and box[2] > left)
        covered = 0.0
        end = float("-inf")
        for lower, upper in intervals:
            covered += max(0.0, upper - max(lower, end))
            end = max(end, upper)
        total += (right - left) * covered
    return total


def overlap(left: Region, right: Region) -> float:
    denominator = min(area(left), area(right))
    return area(intersection(left, right)) / denominator if denominator else 0.0


def covered(region: Region, boxes: Sequence[Region]) -> bool:
    return area(region) > 0 and union_area([intersection(region, box) for box in boxes]) >= area(region) * 0.98


def from_normalized(box: list[float], region: Region) -> Region:
    width, height = region[2] - region[0], region[3] - region[1]
    return (
        region[0] + box[0] * width / 1000,
        region[1] + box[1] * height / 1000,
        region[0] + box[2] * width / 1000,
        region[1] + box[3] * height / 1000,
    )


def tiles(width: float, height: float) -> list[Region]:
    if height > width * 2.5:
        return [
            (0, max(0, height * (i / 4 - 0.025)), width, min(height, height * ((i + 1) / 4 + 0.025))) for i in range(4)
        ]
    if width > height * 2.5:
        return [
            (max(0, width * (i / 4 - 0.025)), 0, min(width, width * ((i + 1) / 4 + 0.025)), height) for i in range(4)
        ]
    return [
        (x0 * width, y0 * height, x1 * width, y1 * height)
        for x0, y0, x1, y1 in ((0, 0, 0.525, 0.525), (0.475, 0, 1, 0.525), (0, 0.475, 0.525, 1), (0.475, 0.475, 1, 1))
    ]
