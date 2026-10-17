from __future__ import annotations

import math
import re
from dataclasses import dataclass

from app.core.common.interview_qa.stage2_nondev.dto import BBox, PageKind, ReadRegion

MAX_REGIONS = 8
MAX_ASPECT = 3.0
OVERLAP = 0.1
TEXT_MIN = 200
BROKEN_RATIO = 0.3


@dataclass(frozen=True)
class PagePolicy:
    text_rich: int = TEXT_MIN
    text_poor: int = 50
    image_some: float = 0.15
    image_figure: float = 0.3
    image_dominant: float = 0.5
    drawings: int = 30
    broken_ratio: float = BROKEN_RATIO
    tile_aspect: float = MAX_ASPECT
    tile_max: int = MAX_REGIONS


DEFAULT_POLICY = PagePolicy()


def classify_page(
    text: str, image_area: float, drawing_count: int, *, has_visual: bool, policy: PagePolicy = DEFAULT_POLICY
) -> PageKind:
    compact = re.sub(r"\s", "", text)
    broken = sum(len(match.group()) for match in re.finditer(r"\(cid:\d+\)", compact))
    broken_chars = sum(char == "\ufffd" or "\ue000" <= char <= "\uf8ff" for char in compact)
    if (broken_chars + broken) / max(1, len(compact)) >= policy.broken_ratio:
        return "F"
    if len(compact) >= policy.text_rich:
        return "B" if has_visual or image_area >= policy.image_figure or drawing_count >= policy.drawings else "A"
    if len(compact) < policy.text_poor and image_area >= policy.image_dominant:
        return "D"
    if (
        len(compact) < policy.text_rich
        and image_area < policy.image_some
        and drawing_count < policy.drawings
        and not has_visual
    ):
        return "C"
    return "E"


def plan_regions(
    page: int, bbox: BBox, policy: PagePolicy = DEFAULT_POLICY, *, dense_table: bool = False
) -> tuple[list[ReadRegion], bool]:
    x0, y0, x1, y1 = bbox
    width, height = x1 - x0, y1 - y0
    if min(width, height) <= 0:
        return [], True
    if max(width, height) / min(width, height) <= policy.tile_aspect:
        if dense_table:
            if policy.tile_max < 4:  # noqa: PLR2004
                return [], True
            # Two equal tiles in each direction with 10% overlap of each tile.
            tile_width, tile_height = width / (2 - OVERLAP), height / (2 - OVERLAP)
            return [
                ReadRegion(page, (x, y, x + tile_width, y + tile_height), tiled=True)
                for y in (y0, y1 - tile_height)
                for x in (x0, x1 - tile_width)
            ], False
        return [ReadRegion(page, bbox)], False
    vertical = height > width
    length, short = max(width, height), min(width, height)
    tile = short * policy.tile_aspect
    step = tile * (1 - OVERLAP)
    count = math.ceil((length - tile) / step) + 1
    if count > policy.tile_max:
        return [], True
    regions = []
    for index in range(count):
        start = min(index * step, length - tile)
        box = (x0, y0 + start, x1, y0 + start + tile) if vertical else (x0 + start, y0, x0 + start + tile, y1)
        regions.append(ReadRegion(page, box, tiled=True))
    return regions, False
