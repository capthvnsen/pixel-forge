"""Shared primitives: coordinates, colours, the shape DSL, transforms, regions."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Vec2 = tuple[int, int]
RGBA = tuple[int, int, int, int]


class ShapeBase(BaseModel):
    """Common fields for every shape op. `color` is a palette color id, not a literal RGBA."""

    model_config = ConfigDict(extra="forbid")

    color: str
    op: str


class PixelShape(ShapeBase):
    op: Literal["pixel"]
    at: Vec2


class LineShape(ShapeBase):
    op: Literal["line"]
    start: Vec2
    end: Vec2


class RectShape(ShapeBase):
    op: Literal["rect"]
    at: Vec2
    size: Vec2
    fill: bool = True


class EllipseShape(ShapeBase):
    op: Literal["ellipse"]
    at: Vec2
    size: Vec2
    fill: bool = True


class PolygonShape(ShapeBase):
    """A closed polygon through `points` (at least 3 vertices, listed in order).

    `fill=True` rasterises the interior with the even-odd rule (so a self-intersecting
    outline or a nested hole drawn as one point list produces holes) and then post-fills the
    closed Bresenham boundary, so every vertex and edge pixel is present and mirrored
    polygons render identical silhouettes; `fill=False` draws just that boundary (each edge
    is a Bresenham line, closing back to the first point).
    """

    op: Literal["polygon"]
    points: list[Vec2]
    fill: bool = True

    @model_validator(mode="after")
    def _check_points(self) -> PolygonShape:
        if len(self.points) < 3:
            raise ValueError("polygon needs at least 3 points")
        return self


class ArcShape(ShapeBase):
    """A circular arc centred at `at` with integer `radius` (px).

    Angles are in degrees, standard math convention: 0 is the +x axis and positive angles
    rotate clockwise in screen coordinates (y down), so 90 is straight down and 270 straight
    up. `start_deg == end_deg` (e.g. the default 0..360) is a full circle. `fill=False` draws
    an annulus band `thickness` px wide centred on the circle; `fill=True` draws a filled
    pie slice from the centre out to `radius`.
    """

    op: Literal["arc"]
    at: Vec2
    radius: int
    start_deg: float = 0
    end_deg: float = 360
    thickness: int = 1
    fill: bool = False

    @model_validator(mode="after")
    def _check_geometry(self) -> ArcShape:
        if self.radius < 0:
            raise ValueError("arc radius must be >= 0")
        if self.thickness < 1:
            raise ValueError("arc thickness must be >= 1")
        return self


class CurveShape(ShapeBase):
    """A polyline through `points` (at least 2), drawn as chained line segments.

    `thickness` (px, default 1) renders every segment as a Euclidean distance band of
    half-width `thickness / 2` centred on the line (uniform width on every slope);
    thickness 1 is a 1 px Bresenham line, and a degenerate single-point curve renders as a
    centred dot.
    """

    op: Literal["curve"]
    points: list[Vec2]
    thickness: int = 1

    @model_validator(mode="after")
    def _check_points(self) -> CurveShape:
        if len(self.points) < 2:
            raise ValueError("curve needs at least 2 points")
        if self.thickness < 1:
            raise ValueError("curve thickness must be >= 1")
        return self


class BezierShape(ShapeBase):
    """A quadratic Bezier curve from `p0`, guided by `p1`, to `p2`.

    Sampled deterministically at a fixed integer count derived from the control-polygon
    length, then drawn as a polyline; `thickness` behaves as in `CurveShape`.
    """

    op: Literal["bezier"]
    p0: Vec2
    p1: Vec2
    p2: Vec2
    thickness: int = 1

    @model_validator(mode="after")
    def _check_thickness(self) -> BezierShape:
        if self.thickness < 1:
            raise ValueError("bezier thickness must be >= 1")
        return self


_TRANSPARENT = (".", " ")


class BitmapShape(BaseModel):
    """A region of palette-indexed pixel data, one character per pixel.

    Carries many colours via `key` rather than the single `color` every other shape has,
    so it does not inherit `ShapeBase`. `.` and a space are always transparent and must not
    appear in `key`.
    """

    model_config = ConfigDict(extra="forbid")

    op: Literal["bitmap"]
    at: Vec2
    key: dict[str, str]
    rows: list[str]

    @model_validator(mode="after")
    def _check_rows_and_key(self) -> BitmapShape:
        if not self.rows:
            raise ValueError("bitmap must have at least one row")
        for i, row in enumerate(self.rows):
            if not row:
                raise ValueError(f"bitmap row {i} is empty")
        width = len(self.rows[0])
        for i, row in enumerate(self.rows):
            if len(row) != width:
                raise ValueError(
                    f"bitmap row {i} has length {len(row)}, expected {width} "
                    "(rows must not be ragged)"
                )
        for char in self.key:
            if len(char) != 1:
                raise ValueError(f"bitmap key {char!r} must be exactly one character")
            if char in _TRANSPARENT:
                raise ValueError(f"bitmap key {char!r} is reserved for transparency")
        used: set[str] = set()
        for i, row in enumerate(self.rows):
            for char in row:
                if char in _TRANSPARENT:
                    continue
                if char not in self.key:
                    raise ValueError(f"bitmap row {i} uses char {char!r} not present in key")
                used.add(char)
        unused = set(self.key) - used
        if unused:
            raise ValueError(f"bitmap key entries unused by any row: {sorted(unused)}")
        return self

    @property
    def width(self) -> int:
        return len(self.rows[0])

    @property
    def height(self) -> int:
        return len(self.rows)


Shape = Annotated[
    PixelShape
    | LineShape
    | RectShape
    | EllipseShape
    | PolygonShape
    | ArcShape
    | CurveShape
    | BezierShape
    | BitmapShape,
    Field(discriminator="op"),
]


class RegionTransform(BaseModel):
    """A per-frame or per-direction-override delta applied to a region."""

    model_config = ConfigDict(extra="forbid")

    offset: Vec2 = (0, 0)
    visible: bool | None = None
    color_swap: dict[str, str] = Field(default_factory=dict)  # palette id -> palette id
    scale_size: Vec2 = (0, 0)  # additive px growth on rect/ellipse sizes


class Region(BaseModel):
    """A named, layered group of shapes anchored at a world-space anchor point.

    Shape coordinates within a region are relative to the region's anchor point,
    not absolute canvas coordinates.
    """

    model_config = ConfigDict(extra="forbid")

    anchor: str
    layer: int
    shapes: list[Shape] = Field(default_factory=list)
    mirror_safe: bool = True
    protected: bool = False


# Anchor points are just named Vec2 positions in a doc's `anchors: dict[str, Vec2]` map.
# Anchor math (world position, mirroring) is domain logic — see domain/geometry.py.
Anchor = Vec2
