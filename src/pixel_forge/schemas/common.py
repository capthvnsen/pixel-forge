"""Shared primitives: coordinates, colours, the shape DSL, transforms, regions."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

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


Shape = Annotated[
    PixelShape | LineShape | RectShape | EllipseShape,
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
