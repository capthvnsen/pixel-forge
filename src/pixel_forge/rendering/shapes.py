"""Shape DSL -> pixel writes.

Dispatches on `shape.op` via duck typing (`getattr` reads of the documented attribute names)
so this module never imports the pydantic schema models at runtime — `pixel_forge.schemas.common`
is authored by another agent concurrently and must not be a runtime dependency here.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING

from pixel_forge.errors import RenderError
from pixel_forge.rendering.canvas import RGBA, Canvas, Vec2

if TYPE_CHECKING:
    from pixel_forge.schemas.common import BitmapShape, Shape

_TRANSPARENT = (".", " ")


def _offset(point: Vec2, origin: Vec2) -> Vec2:
    return (point[0] + origin[0], point[1] + origin[1])


def draw_shape(canvas: Canvas, shape: Shape, origin: Vec2, rgba: RGBA) -> None:
    """Render one shape, with every shape-local coordinate offset by `origin`.

    Reads shape-local fields via getattr rather than direct attribute access: `shape` is
    statically a union of all shape models, and only one variant actually carries any
    given field (e.g. only PixelShape has `.at`), so direct access would fail mypy's
    union-attr check for the other members. noqa: B009 — this indirection is intentional.
    """
    op: str = shape.op
    if op == "pixel":
        at: Vec2 = getattr(shape, "at")  # noqa: B009
        canvas.set_pixel(*_offset(at, origin), rgba)
    elif op == "line":
        start: Vec2 = getattr(shape, "start")  # noqa: B009
        end: Vec2 = getattr(shape, "end")  # noqa: B009
        canvas.draw_line(_offset(start, origin), _offset(end, origin), rgba)
    elif op == "rect":
        at = getattr(shape, "at")  # noqa: B009
        size: Vec2 = getattr(shape, "size")  # noqa: B009
        fill: bool = getattr(shape, "fill")  # noqa: B009
        canvas.draw_rect(_offset(at, origin), size, rgba, fill)
    elif op == "ellipse":
        at = getattr(shape, "at")  # noqa: B009
        size = getattr(shape, "size")  # noqa: B009
        fill = getattr(shape, "fill")  # noqa: B009
        canvas.draw_ellipse(_offset(at, origin), size, rgba, fill)
    elif op == "polygon":
        points: list[Vec2] = getattr(shape, "points")  # noqa: B009
        fill = getattr(shape, "fill")  # noqa: B009
        canvas.draw_polygon([_offset(p, origin) for p in points], rgba, fill)
    elif op == "arc":
        at = getattr(shape, "at")  # noqa: B009
        radius: int = getattr(shape, "radius")  # noqa: B009
        start_deg: float = getattr(shape, "start_deg")  # noqa: B009
        end_deg: float = getattr(shape, "end_deg")  # noqa: B009
        thickness: int = getattr(shape, "thickness")  # noqa: B009
        fill = getattr(shape, "fill")  # noqa: B009
        canvas.draw_arc(
            _offset(at, origin), radius, start_deg, end_deg, rgba, thickness, fill
        )
    elif op == "curve":
        points = getattr(shape, "points")  # noqa: B009
        thickness = getattr(shape, "thickness")  # noqa: B009
        canvas.draw_polyline([_offset(p, origin) for p in points], rgba, thickness)
    elif op == "bezier":
        p0: Vec2 = getattr(shape, "p0")  # noqa: B009
        p1: Vec2 = getattr(shape, "p1")  # noqa: B009
        p2: Vec2 = getattr(shape, "p2")  # noqa: B009
        thickness = getattr(shape, "thickness")  # noqa: B009
        canvas.draw_bezier(
            _offset(p0, origin), _offset(p1, origin), _offset(p2, origin), rgba, thickness
        )
    elif op == "bitmap":
        raise RenderError(
            "bitmap shapes carry many colours and must be drawn with draw_bitmap(), not "
            "draw_shape()"
        )
    else:
        raise RenderError(f"unknown shape op: {op!r}")


def draw_bitmap(
    canvas: Canvas, shape: BitmapShape, origin: Vec2, colors: Mapping[str, RGBA]
) -> None:
    """Draw a bitmap shape row by row, `shape.at` offset by `origin`.

    `colors` maps each `key` character directly to its resolved RGBA (i.e. `shape.key` with
    its palette-id values already looked up by the caller). `.` and space are transparent
    and are skipped entirely rather than drawn, so they never erase whatever is already on
    `canvas` beneath them. Out-of-bounds pixels clip silently via `Canvas.set_pixel`.
    """
    ax, ay = _offset(shape.at, origin)
    for row_index, row in enumerate(shape.rows):
        for col_index, char in enumerate(row):
            if char in _TRANSPARENT:
                continue
            canvas.set_pixel(ax + col_index, ay + row_index, colors[char])


def bitmap_size(shape: BitmapShape) -> Vec2:
    """The (width, height) of `shape`'s pixel grid, ignoring `at`."""
    return (len(shape.rows[0]), len(shape.rows))


def shape_bounds(shape: Shape, origin: Vec2) -> tuple[int, int, int, int]:
    """The half-open (x0, y0, x1, y1) bounding box a shape would touch, for use by validators."""
    op: str = shape.op
    if op == "pixel":
        x, y = _offset(getattr(shape, "at"), origin)  # noqa: B009
        return (x, y, x + 1, y + 1)
    if op == "line":
        x0, y0 = _offset(getattr(shape, "start"), origin)  # noqa: B009
        x1, y1 = _offset(getattr(shape, "end"), origin)  # noqa: B009
        x_lo, x_hi = sorted((x0, x1))
        y_lo, y_hi = sorted((y0, y1))
        return (x_lo, y_lo, x_hi + 1, y_hi + 1)
    if op in ("rect", "ellipse"):
        x, y = _offset(getattr(shape, "at"), origin)  # noqa: B009
        w, h = getattr(shape, "size")  # noqa: B009
        return (x, y, x + max(w, 0), y + max(h, 0))
    if op == "polygon":
        pts: list[Vec2] = getattr(shape, "points")  # noqa: B009
        xs = [_offset(p, origin)[0] for p in pts]
        ys = [_offset(p, origin)[1] for p in pts]
        return (min(xs), min(ys), max(xs) + 1, max(ys) + 1)
    if op == "arc":
        x, y = _offset(getattr(shape, "at"), origin)  # noqa: B009
        r: int = getattr(shape, "radius")  # noqa: B009
        t: int = getattr(shape, "thickness")  # noqa: B009
        start_deg: float = getattr(shape, "start_deg")  # noqa: B009
        end_deg: float = getattr(shape, "end_deg")  # noqa: B009
        fill: bool = getattr(shape, "fill")  # noqa: B009
        # The band is the annulus [r - t/2, r + t/2] (doubled-integer scheme in
        # draw_arc), so the outer lattice extent is (2r + t) // 2, not r + t.
        sweep = (end_deg - start_deg) % 360.0
        if sweep == 0.0:  # full circle
            ext = (2 * r + t) // 2
            return (x - ext, y - ext, x + ext + 1, y + ext + 1)
        candidates = [start_deg, end_deg]
        for axis in (0.0, 90.0, 180.0, 270.0):
            if (axis - start_deg) % 360.0 <= sweep:
                candidates.append(axis)
        xs_f: list[float] = [x + r * math.cos(math.radians(deg)) for deg in candidates]
        ys_f: list[float] = [y + r * math.sin(math.radians(deg)) for deg in candidates]
        k = t / 2.0
        x0 = math.ceil(min(xs_f) - k)
        x1 = math.floor(max(xs_f) + k) + 1
        y0 = math.ceil(min(ys_f) - k)
        y1 = math.floor(max(ys_f) + k) + 1
        if fill:  # a pie slice reaches the centre
            x0, x1 = min(x0, x), max(x1, x + 1)
            y0, y1 = min(y0, y), max(y1, y + 1)
        return (x0, y0, x1, y1)
    if op in ("curve", "bezier"):
        thickness = getattr(shape, "thickness")  # noqa: B009
        if op == "curve":
            pts = getattr(shape, "points")  # noqa: B009
            xs = [_offset(p, origin)[0] for p in pts]
            ys = [_offset(p, origin)[1] for p in pts]
            x_lo, x_hi = min(xs), max(xs)
            y_lo, y_hi = min(ys), max(ys)
        else:
            p0 = _offset(getattr(shape, "p0"), origin)  # noqa: B009
            p1 = _offset(getattr(shape, "p1"), origin)  # noqa: B009
            p2 = _offset(getattr(shape, "p2"), origin)  # noqa: B009
            # True curve extrema: endpoints plus any interior t where the derivative is
            # zero, t = (p0 - p1) / (p0 - 2p1 + p2) per axis, clamped to [0, 1].
            # Bounding the control hull would over-estimate by the p1 excursion.
            x_lo_f: float = min(p0[0], p2[0])
            x_hi_f: float = max(p0[0], p2[0])
            y_lo_f: float = min(p0[1], p2[1])
            y_hi_f: float = max(p0[1], p2[1])

            def _axis_extremum(c0: float, c1: float, c2: float) -> float | None:
                denom = c0 - 2 * c1 + c2
                if denom == 0:
                    return None
                t = (c0 - c1) / denom
                if not 0.0 < t < 1.0:
                    return None
                mt = 1.0 - t
                return mt * mt * c0 + 2.0 * mt * t * c1 + t * t * c2

            for axis in (0, 1):
                v = _axis_extremum(p0[axis], p1[axis], p2[axis])
                if v is not None:
                    if axis == 0:
                        x_lo_f, x_hi_f = min(x_lo_f, v), max(x_hi_f, v)
                    else:
                        y_lo_f, y_hi_f = min(y_lo_f, v), max(y_hi_f, v)
        # The distance-band stroke extends thickness / 2 beyond the curve: lattice
        # pixels span [ceil(min - t/2), floor(max + t/2)].
        k = thickness / 2.0
        if op == "bezier":
            return (
                math.ceil(x_lo_f - k),
                math.ceil(y_lo_f - k),
                math.floor(x_hi_f + k) + 1,
                math.floor(y_hi_f + k) + 1,
            )
        return (
            math.ceil(x_lo - k),
            math.ceil(y_lo - k),
            math.floor(x_hi + k) + 1,
            math.floor(y_hi + k) + 1,
        )
    if op == "bitmap":
        x, y = _offset(getattr(shape, "at"), origin)  # noqa: B009
        rows: list[str] = getattr(shape, "rows")  # noqa: B009
        return (x, y, x + len(rows[0]), y + len(rows))
    raise RenderError(f"unknown shape op: {op!r}")
