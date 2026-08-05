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
    PixelShape | LineShape | RectShape | EllipseShape | BitmapShape,
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
