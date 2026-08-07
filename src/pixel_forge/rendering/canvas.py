"""Deterministic numpy-backed RGBA raster primitives.

No antialiasing, no float coordinates, no PIL resampling other than NEAREST.
Alpha is always strictly 0 or 255.
"""

from __future__ import annotations

import math
from itertools import pairwise
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PIL import Image

Vec2 = tuple[int, int]
RGBA = tuple[int, int, int, int]

#: Fractional bits (as a scale factor) for `Canvas.rotate`'s fixed-point cos/sin.
_ROTATE_FIXED_SCALE = 1 << 14


def _round_half_away_from_zero(value: float) -> int:
    if value >= 0:
        return math.floor(value + 0.5)
    return math.ceil(value - 0.5)


def _fixed_div_round(num: NDArray[np.int64], scale: int) -> NDArray[np.int64]:
    """`num / scale` rounded to nearest, halves away from zero, pure integer.

    Elementwise over the fixed-point numerator array; both branches of the
    `np.where` are exact integer floor-division, so the result is identical on
    every run regardless of evaluation order.
    """
    half = scale // 2
    return np.where(num >= 0, (num + half) // scale, -((-num + half) // scale))


class Canvas:
    """A single (h, w, 4) uint8 RGBA raster, origin top-left, initialised transparent."""

    def __init__(self, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            raise ValueError("Canvas dimensions must be positive")
        self._width = width
        self._height = height
        self._array: NDArray[np.uint8] = np.zeros((height, width, 4), dtype=np.uint8)

    @property
    def array(self) -> NDArray[np.uint8]:
        """The underlying (h, w, 4) uint8 RGBA array. Mutable and live: mutating it mutates
        this Canvas in place."""
        return self._array

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    def set_pixel(self, x: int, y: int, rgba: RGBA) -> None:
        if rgba[3] not in (0, 255):
            raise ValueError("alpha must be 0 or 255")
        if x < 0 or x >= self._width or y < 0 or y >= self._height:
            return
        self._array[y, x] = rgba

    def get_pixel(self, x: int, y: int) -> RGBA:
        r, g, b, a = self._array[y, x]
        return (int(r), int(g), int(b), int(a))

    def draw_line(self, a: Vec2, b: Vec2, rgba: RGBA) -> None:
        """Integer Bresenham line, endpoints inclusive."""
        x0, y0 = a
        x1, y1 = b
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        x, y = x0, y0
        while True:
            self.set_pixel(x, y, rgba)
            if x == x1 and y == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x += sx
            if e2 <= dx:
                err += dx
                y += sy

    def draw_rect(self, at: Vec2, size: Vec2, rgba: RGBA, fill: bool) -> None:
        x0, y0 = at
        w, h = size
        if w <= 0 or h <= 0:
            return
        if fill:
            for y in range(y0, y0 + h):
                for x in range(x0, x0 + w):
                    self.set_pixel(x, y, rgba)
        else:
            for x in range(x0, x0 + w):
                self.set_pixel(x, y0, rgba)
                self.set_pixel(x, y0 + h - 1, rgba)
            for y in range(y0, y0 + h):
                self.set_pixel(x0, y, rgba)
                self.set_pixel(x0 + w - 1, y, rgba)

    def draw_ellipse(self, at: Vec2, size: Vec2, rgba: RGBA, fill: bool) -> None:
        """Integer midpoint ellipse inscribed in the at/size bounding box.

        Inclusion test uses doubled, box-centred integer coordinates so the result is exactly
        symmetric horizontally and vertically for both even and odd sizes.
        """
        x0, y0 = at
        w, h = size
        if w <= 0 or h <= 0:
            return
        limit = (w * h) ** 2
        mask = np.zeros((h, w), dtype=bool)
        for y in range(h):
            ny = 2 * y - (h - 1)
            for x in range(w):
                nx = 2 * x - (w - 1)
                if (nx * h) ** 2 + (ny * w) ** 2 <= limit:
                    mask[y, x] = True
        if fill:
            target = mask
        else:
            target = np.zeros_like(mask)
            for y in range(h):
                for x in range(w):
                    if not mask[y, x]:
                        continue
                    on_edge = (
                        x == 0
                        or x == w - 1
                        or y == 0
                        or y == h - 1
                        or not mask[y - 1, x]
                        or not mask[y + 1, x]
                        or not mask[y, x - 1]
                        or not mask[y, x + 1]
                    )
                    if on_edge:
                        target[y, x] = True
        ys, xs = np.nonzero(target)
        for yy, xx in zip(ys.tolist(), xs.tolist(), strict=True):
            self.set_pixel(x0 + xx, y0 + yy, rgba)

    def draw_polygon(self, points: list[Vec2], rgba: RGBA, fill: bool = True) -> None:
        """Draw a closed polygon through `points` (>= 3 vertices, listed in order).

        `fill=True` rasterises the interior with the even-odd rule via scanline crossings at
        each pixel centre; `fill=False` draws only the outline (Bresenham edges, closed back
        to the first vertex). Degenerate inputs (< 3 points) draw nothing.
        """
        if len(points) < 3:
            return
        if fill:
            self._fill_polygon(points, rgba)
        else:
            for i in range(len(points)):
                self.draw_line(points[i], points[(i + 1) % len(points)], rgba)

    def _fill_polygon(self, points: list[Vec2], rgba: RGBA) -> None:
        """Even-odd scanline fill plus the closed outline.

        The scanline pass rasterises interior pixels on the lattice rows (pixels are lattice
        points at integer coordinates, the same convention as every other primitive here):
        for every scanline the polygon is intersected with the horizontal line y (not
        y + 0.5), crossings are sorted, and the pixels strictly between each pair are
        filled. Edges are half-open on y (`min <= y < max`) so a vertex shared by two edges
        is counted by exactly one of them, and horizontal edges contribute nothing. Because
        the crossing pairs of a symmetric polygon mirror exactly, the interior is symmetric.

        The outline pass then post-fills the closed Bresenham boundary (identical to
        `fill=False`), so `fill=True` agrees with `fill=False` at every vertex and edge
        pixel: mirrored polygons render identical silhouettes, and no vertex is chopped.
        """
        ys = [p[1] for p in points]
        n = len(points)
        for y in range(min(ys), max(ys) + 1):
            crossings: list[float] = []
            for i in range(n):
                x0, y0 = points[i]
                x1, y1 = points[(i + 1) % n]
                if y0 == y1 or not (min(y0, y1) <= y < max(y0, y1)):
                    continue
                t = (y - y0) / (y1 - y0)
                crossings.append(x0 + t * (x1 - x0))
            crossings.sort()
            for i in range(0, len(crossings) - 1, 2):
                x_start = math.floor(crossings[i]) + 1  # strictly inside the pair
                x_end = math.ceil(crossings[i + 1]) - 1
                for x in range(x_start, x_end + 1):
                    self.set_pixel(x, y, rgba)
        for i in range(n):
            self.draw_line(points[i], points[(i + 1) % n], rgba)

    def draw_polyline(self, points: list[Vec2], rgba: RGBA, thickness: int = 1) -> None:
        """Draw chained line segments through `points` (>= 1).

        `thickness` (px) renders every segment as the Euclidean distance band of half-width
        `thickness / 2` centred on the mathematical line, so the stroke has uniform width on
        every slope and is symmetric about the line. Thickness 1 is exactly `draw_line`
        (Bresenham, 1 px wide). A single point renders as a centred dot (disc of radius
        `thickness / 2`).
        """
        if not points or thickness < 1:
            return
        if thickness == 1:
            if len(points) == 1:
                self.set_pixel(*points[0], rgba)
                return
            for a, b in pairwise(points):
                self.draw_line(a, b, rgba)
            return
        if len(points) == 1:
            self._stamp_dot(*points[0], rgba, thickness)
            return
        for a, b in pairwise(points):
            self._draw_thick_line(a, b, rgba, thickness)

    def _draw_thick_line(self, a: Vec2, b: Vec2, rgba: RGBA, thickness: int) -> None:
        """Distance-band thick line (`thickness` >= 2): every pixel whose distance to the
        segment is <= `thickness / 2`, computed exactly with doubled-integer arithmetic.

        For a pixel P and segment AB with d = (dx, dy), u = P - A, len2 = d.d:
        - projection u.d <= 0: closest point is A, include iff 4 |u|^2 <= t^2;
        - projection u.d >= len2: closest point is B, include iff 4 |P - B|^2 <= t^2;
        - otherwise the foot is interior: distance = |u x d| / sqrt(len2), include iff
          4 (u x d)^2 <= t^2 len2.
        Only the segment's bounding box grown by `thickness // 2` on each side is tested —
        no pixel inside the band can lie further out (proved for both odd and even t).
        """
        x0, y0 = a
        x1, y1 = b
        dx = x1 - x0
        dy = y1 - y0
        len2 = dx * dx + dy * dy
        if len2 == 0:
            self._stamp_dot(x0, y0, rgba, thickness)
            return
        k = thickness // 2
        t2 = thickness * thickness
        x_lo, x_hi = min(x0, x1), max(x0, x1)
        y_lo, y_hi = min(y0, y1), max(y0, y1)
        for py in range(y_lo - k, y_hi + k + 1):
            for px in range(x_lo - k, x_hi + k + 1):
                ux = px - x0
                uy = py - y0
                dot = ux * dx + uy * dy
                if dot <= 0:
                    if 4 * (ux * ux + uy * uy) <= t2:
                        self.set_pixel(px, py, rgba)
                elif dot >= len2:
                    vx = px - x1
                    vy = py - y1
                    if 4 * (vx * vx + vy * vy) <= t2:
                        self.set_pixel(px, py, rgba)
                else:
                    cross = ux * dy - uy * dx
                    if 4 * cross * cross <= t2 * len2:
                        self.set_pixel(px, py, rgba)

    def _stamp_dot(self, x: int, y: int, rgba: RGBA, thickness: int) -> None:
        """Stamp a centred dot: every pixel within `thickness / 2` of (x, y), exact integer
        test (disc of radius `thickness / 2`). Thickness 1 is the single pixel itself."""
        k = thickness // 2
        t2 = thickness * thickness
        for py in range(y - k, y + k + 1):
            for px in range(x - k, x + k + 1):
                ux = px - x
                uy = py - y
                if 4 * (ux * ux + uy * uy) <= t2:
                    self.set_pixel(px, py, rgba)

    def draw_bezier(self, p0: Vec2, p1: Vec2, p2: Vec2, rgba: RGBA, thickness: int = 1) -> None:
        """Quadratic Bezier from `p0` via `p1` to `p2`.

        The sample count is fixed and integer: the even count 2 * L (L = integer Manhattan
        length of the control polygon) forced odd, so the same shape always samples the same
        points, no float-derived count, and the curve midpoint t = 1/2 is always sampled
        exactly (important for symmetric curves whose apex sits at the midpoint). Endpoints
        p0 and p2 are always included. `thickness` behaves as in `draw_polyline`:
        thickness 1 is drawn from the rounded samples as Bresenham segments; thickness >= 2
        strokes the band around the unrounded samples, so the stroke follows the true curve
        instead of the rounded polygon (rounding samples to integers would deviate by up to
        half a pixel and occasionally widen a column by one).
        """
        if thickness < 1:
            return
        distance = abs(p1[0] - p0[0]) + abs(p1[1] - p0[1]) + abs(p2[0] - p1[0]) + abs(p2[1] - p1[1])
        n = max(1, int(distance * 2)) | 1  # odd: the midpoint is always sampled
        if n < 2:
            self._stamp_dot(*p0, rgba, thickness)
            return
        if thickness == 1:
            sampled: list[Vec2] = []
            for i in range(n):
                t = i / (n - 1)
                mt = 1.0 - t
                x = round(mt * mt * p0[0] + 2.0 * mt * t * p1[0] + t * t * p2[0])
                y = round(mt * mt * p0[1] + 2.0 * mt * t * p1[1] + t * t * p2[1])
                sampled.append((x, y))
            self.draw_polyline(sampled, rgba, thickness)
            return
        f0 = (float(p0[0]), float(p0[1]))
        f1 = (float(p1[0]), float(p1[1]))
        f2 = (float(p2[0]), float(p2[1]))
        prev: tuple[float, float] = f0
        for i in range(1, n):
            t = i / (n - 1)
            mt = 1.0 - t
            fx: float = mt * mt * f0[0] + 2.0 * mt * t * f1[0] + t * t * f2[0]
            fy: float = mt * mt * f0[1] + 2.0 * mt * t * f1[1] + t * t * f2[1]
            self._draw_thick_curve_segment(prev, (fx, fy), rgba, thickness)
            prev = (fx, fy)

    def _draw_thick_curve_segment(
        self,
        a: tuple[float, float],
        b: tuple[float, float],
        rgba: RGBA,
        thickness: int,
    ) -> None:
        """Distance-band stroke over one float-endpoint segment (`thickness` >= 2).

        Same inclusion test as `_draw_thick_line` (pixels within `thickness / 2` of the
        segment) but with float endpoints, used for bezier flattening where the samples are
        unrounded curve points. Deterministic: the arithmetic is fixed IEEE-754 float64
        operations, and every operand is a product/sum of small integers or fractions of
        them, so results are exactly reproducible run to run.
        """
        ax, ay = a
        bx, by = b
        dx = bx - ax
        dy = by - ay
        len2 = dx * dx + dy * dy
        if len2 == 0.0:
            return
        k = thickness // 2
        t2 = thickness * thickness
        x_lo = math.floor(min(ax, bx) - k)
        x_hi = math.ceil(max(ax, bx) + k)
        y_lo = math.floor(min(ay, by) - k)
        y_hi = math.ceil(max(ay, by) + k)
        for py in range(y_lo, y_hi + 1):
            for px in range(x_lo, x_hi + 1):
                ux = px - ax
                uy = py - ay
                dot = ux * dx + uy * dy
                if dot <= 0.0:
                    if 4.0 * (ux * ux + uy * uy) <= t2:
                        self.set_pixel(px, py, rgba)
                elif dot >= len2:
                    vx = px - bx
                    vy = py - by
                    if 4.0 * (vx * vx + vy * vy) <= t2:
                        self.set_pixel(px, py, rgba)
                else:
                    cross = ux * dy - uy * dx
                    if 4.0 * cross * cross <= t2 * len2:
                        self.set_pixel(px, py, rgba)

    def draw_arc(
        self,
        at: Vec2,
        radius: int,
        start_deg: float,
        end_deg: float,
        rgba: RGBA,
        thickness: int = 1,
        fill: bool = False,
    ) -> None:
        """Draw a circular arc centred at `at` in the `radius`-px circle.

        Angles are degrees, 0 = +x, positive = clockwise in screen coords (y down);
        `start_deg == end_deg` (mod 360) is a full circle. `fill=False` draws the annulus
        band `thickness` px wide centred on the circle; `fill=True` draws a filled pie slice
        from the centre out to `radius`. Inclusion tests use doubled integer coordinates so
        the result is exactly symmetric and needs no float comparisons against pixel edges.
        """
        cx, cy = at
        if radius < 0 or thickness < 1:
            return
        sweep = (end_deg - start_deg) % 360.0
        full = sweep == 0.0
        r_ext = radius + thickness
        if full:
            x0, x1 = cx - r_ext, cx + r_ext
            y0, y1 = cy - r_ext, cy + r_ext
        else:
            # Extremal arc points: both endpoints plus any axis angle inside the sweep.
            candidates = [start_deg, end_deg]
            for axis in (0.0, 90.0, 180.0, 270.0):
                if (axis - start_deg) % 360.0 <= sweep:
                    candidates.append(axis)
            xs: list[float] = []
            ys: list[float] = []
            for deg in candidates:
                rad = math.radians(deg)
                xs.append(cx + radius * math.cos(rad))
                ys.append(cy + radius * math.sin(rad))
            x0 = math.floor(min(xs)) - thickness
            x1 = math.ceil(max(xs)) + thickness
            y0 = math.floor(min(ys)) - thickness
            y1 = math.ceil(max(ys)) + thickness
            if fill:  # a pie slice reaches the centre
                x0, x1 = min(x0, cx), max(x1, cx)
                y0, y1 = min(y0, cy), max(y1, cy)
        radius2 = (2 * radius) ** 2
        inner2 = max(2 * radius - thickness, 0) ** 2
        outer2 = (2 * radius + thickness) ** 2
        for py in range(y0, y1 + 1):
            for px in range(x0, x1 + 1):
                qx = 2 * (px - cx)
                qy = 2 * (py - cy)
                d2 = qx * qx + qy * qy
                if fill:
                    if d2 > radius2:
                        continue
                elif not (inner2 <= d2 <= outer2):
                    continue
                if not full:
                    ang = math.degrees(math.atan2(qy, qx)) % 360.0
                    if (ang - start_deg) % 360.0 > sweep:
                        continue
                self.set_pixel(px, py, rgba)

    def blit(self, other: Canvas, offset: Vec2) -> None:
        """Source-over with binary alpha: destination is replaced where source alpha is 255,
        left untouched where 0. Clips at the edges of this canvas."""
        ox, oy = offset
        sh, sw = other.array.shape[:2]
        sx0, sy0 = max(0, -ox), max(0, -oy)
        sx1, sy1 = min(sw, self._width - ox), min(sh, self._height - oy)
        if sx0 >= sx1 or sy0 >= sy1:
            return
        dx0, dy0 = sx0 + ox, sy0 + oy
        dx1, dy1 = sx1 + ox, sy1 + oy
        src_region = other.array[sy0:sy1, sx0:sx1]
        dest_region = self._array[dy0:dy1, dx0:dx1]
        opaque = src_region[..., 3] == 255
        dest_region[opaque] = src_region[opaque]

    def mirror_x(self) -> Canvas:
        c = Canvas(self._width, self._height)
        c._array[:] = np.fliplr(self._array)
        return c

    def rotate(self, pivot: Vec2, angle_deg: float) -> Canvas:
        """New canvas of the same size, contents rotated `angle_deg` about `pivot`.

        Positive angles rotate clockwise in screen coordinates (y down) — the same
        convention as `draw_arc`. Pixels are lattice points at integer coordinates
        (the module-wide convention), so the pixel exactly at `pivot` maps to itself
        at every angle.

        Nearest-neighbour inverse mapping: each destination pixel pulls the source
        pixel nearest its inverse-rotated position, so only colours already on the
        canvas appear, alpha stays strictly 0/255, pixels rotated out of bounds are
        clipped, and vacated pixels stay transparent. The per-pixel decision math is
        pure integer fixed-point (14 fractional bits): cos/sin are converted to
        integers once per call, then every pixel maps with integer multiply/add and
        a round-to-nearest (halves away from zero) division — no float-order
        variance between runs. Angles that are an exact multiple of 90 use exact
        integer coefficients, so quarter turns are pixel-exact; `angle_deg` congruent
        to 0 (mod 360) is a byte-exact identity copy.
        """
        result = Canvas(self._width, self._height)
        angle = angle_deg % 360.0
        if angle == 0.0:
            result._array[:] = self._array
            return result
        scale = _ROTATE_FIXED_SCALE
        if angle == 90.0:
            cos_f, sin_f = 0, scale
        elif angle == 180.0:
            cos_f, sin_f = -scale, 0
        elif angle == 270.0:
            cos_f, sin_f = 0, -scale
        else:
            radians = math.radians(angle)
            cos_f = _round_half_away_from_zero(math.cos(radians) * scale)
            sin_f = _round_half_away_from_zero(math.sin(radians) * scale)
        px, py = pivot
        h, w = self._height, self._width
        ys, xs = np.indices((h, w), dtype=np.int64)
        dx = xs - px
        dy = ys - py
        # Inverse rotation by angle: src_offset = R(-a) @ dst_offset.
        src_x = px + _fixed_div_round(cos_f * dx + sin_f * dy, scale)
        src_y = py + _fixed_div_round(cos_f * dy - sin_f * dx, scale)
        valid = (src_x >= 0) & (src_x < w) & (src_y >= 0) & (src_y < h)
        result._array[ys[valid], xs[valid]] = self._array[src_y[valid], src_x[valid]]
        return result

    def translate(self, offset: Vec2) -> Canvas:
        """New canvas of the same size, contents shifted by offset. The vacated area is
        transparent and any content pushed off-canvas is discarded."""
        c = Canvas(self._width, self._height)
        c.blit(self, offset)
        return c

    def replace_color(self, src: RGBA, dst: RGBA) -> Canvas:
        c = self.copy()
        match = np.all(c._array == np.array(src, dtype=np.uint8), axis=-1)
        c._array[match] = np.array(dst, dtype=np.uint8)
        return c

    def colors(self) -> set[RGBA]:
        """Distinct non-transparent colours present on the canvas."""
        mask = self._array[..., 3] != 0
        pixels = self._array[mask]
        return {(int(p[0]), int(p[1]), int(p[2]), int(p[3])) for p in pixels}

    def bbox(self) -> tuple[int, int, int, int] | None:
        """Half-open (x0, y0, x1, y1) bounds of non-transparent pixels, None when empty."""
        mask = self._array[..., 3] != 0
        if not mask.any():
            return None
        ys, xs = np.nonzero(mask)
        return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)

    def opaque_count(self) -> int:
        return int(np.count_nonzero(self._array[..., 3] == 255))

    def copy(self) -> Canvas:
        c = Canvas(self._width, self._height)
        c._array[:] = self._array
        return c

    def to_image(self) -> Image.Image:
        return Image.fromarray(self._array, mode="RGBA")

    @classmethod
    def from_image(cls, img: Image.Image) -> Canvas:
        rgba = img.convert("RGBA")
        c = cls(rgba.width, rgba.height)
        c._array[:] = np.array(rgba, dtype=np.uint8)
        return c

    def save_png(self, path: Path) -> None:
        """Write a PNG deterministically: no compression-time metadata, so two runs against
        the same pixels produce byte-identical files."""
        self.to_image().save(path, format="PNG", optimize=False)

    def scale(self, factor: int) -> Canvas:
        """Integer nearest-neighbour upscale by repeating each pixel factor x factor times."""
        if type(factor) is not int or factor < 1:
            raise ValueError("scale factor must be a positive int")
        arr = np.repeat(np.repeat(self._array, factor, axis=0), factor, axis=1)
        c = Canvas(self._width * factor, self._height * factor)
        c._array[:] = arr
        return c

    def equals(self, other: Canvas) -> bool:
        return bool(np.array_equal(self._array, other._array))
