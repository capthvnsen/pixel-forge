"""Anchor math, rectangles, bounding boxes, and silhouette measurements."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from pixel_forge.errors import ForgeError
from pixel_forge.schemas.common import Vec2


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    @property
    def is_empty(self) -> bool:
        return self.w <= 0 or self.h <= 0

    def union(self, other: Rect) -> Rect:
        if self.is_empty:
            return other
        if other.is_empty:
            return self
        x0 = min(self.x, other.x)
        y0 = min(self.y, other.y)
        x1 = max(self.right, other.right)
        y1 = max(self.bottom, other.bottom)
        return Rect(x0, y0, x1 - x0, y1 - y0)

    def intersects(self, other: Rect) -> bool:
        if self.is_empty or other.is_empty:
            return False
        return (
            self.x < other.right
            and other.x < self.right
            and self.y < other.bottom
            and other.y < self.bottom
        )

    def contains_point(self, point: Vec2) -> bool:
        x, y = point
        return self.x <= x < self.right and self.y <= y < self.bottom

    def translated(self, dx: int, dy: int) -> Rect:
        return Rect(self.x + dx, self.y + dy, self.w, self.h)


def anchor_world_pos(anchors: Mapping[str, Vec2], name: str, offset: Vec2 = (0, 0)) -> Vec2:
    if name not in anchors:
        raise ForgeError(f"unknown anchor: {name!r}; defined anchors: {sorted(anchors)}")
    ax, ay = anchors[name]
    ox, oy = offset
    return (ax + ox, ay + oy)


def mirror_point_x(point: Vec2, canvas_width: int) -> Vec2:
    """Mirror an x coordinate across the canvas for a mirrored direction.

    Convention: `x' = canvas_width - 1 - x`. This flips about the canvas's
    centre column so it is the exact inverse of `Canvas.mirror_x` (`np.fliplr`)
    for both even and odd widths. Rendering's mirrored-direction path depends
    on this exact formula; do not change it without updating the renderer.
    """
    x, y = point
    return (canvas_width - 1 - x, y)


def mirror_anchors(anchors: Mapping[str, Vec2], canvas_width: int) -> dict[str, Vec2]:
    return {name: mirror_point_x(pos, canvas_width) for name, pos in anchors.items()}


def bbox_of_points(points: Iterable[Vec2]) -> Rect | None:
    pts = list(points)
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    return Rect(x0, y0, x1 - x0 + 1, y1 - y0 + 1)


def silhouette_area(mask: NDArray[np.bool_]) -> int:
    return int(np.count_nonzero(mask))


def silhouette_centroid(mask: NDArray[np.bool_]) -> tuple[float, float] | None:
    """Centroid of a boolean (h, w) mask as (x, y), or `None` when the mask is empty."""
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return None
    return (float(xs.mean()), float(ys.mean()))
