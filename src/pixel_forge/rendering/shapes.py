"""Shape DSL -> pixel writes.

Dispatches on `shape.op` via duck typing (`getattr` reads of the documented attribute names)
so this module never imports the pydantic schema models at runtime — `pixel_forge.schemas.common`
is authored by another agent concurrently and must not be a runtime dependency here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pixel_forge.errors import RenderError
from pixel_forge.rendering.canvas import RGBA, Canvas, Vec2

if TYPE_CHECKING:
    from pixel_forge.schemas.common import Shape


def _offset(point: Vec2, origin: Vec2) -> Vec2:
    return (point[0] + origin[0], point[1] + origin[1])


def draw_shape(canvas: Canvas, shape: Shape, origin: Vec2, rgba: RGBA) -> None:
    """Render one shape, with every shape-local coordinate offset by `origin`.

    Reads shape-local fields via getattr rather than direct attribute access: `shape` is
    statically a union of all four shape models, and only one variant actually carries any
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
    else:
        raise RenderError(f"unknown shape op: {op!r}")


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
    raise RenderError(f"unknown shape op: {op!r}")
