"""Layer stack -> composed frame.

`plan_layers` turns a region map + resolved anchors + per-region transforms into an
ordered, transform-adjusted draw plan (`LayerDraw`). `composite` draws that plan onto a
fresh transparent canvas. Splitting the two lets `rendering.local` reuse `plan_layers`
on region subsets (mirror-safe vs mirror-unsafe) before compositing them separately.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pixel_forge.domain.geometry import anchor_world_pos
from pixel_forge.domain.palette import ResolvedPalette
from pixel_forge.errors import RenderError
from pixel_forge.rendering.canvas import Canvas, Vec2
from pixel_forge.rendering.shapes import draw_shape
from pixel_forge.schemas.asset import BaseAssetDoc
from pixel_forge.schemas.common import EllipseShape, RectShape, Region, RegionTransform, Shape


@dataclass(frozen=True)
class LayerDraw:
    region: str
    layer: int
    origin: Vec2  # resolved anchor world position + transform offset
    shapes: tuple[Shape, ...]  # transform-adjusted shapes


def _apply_color_swap(
    shape: Shape, color_swap: Mapping[str, str], palette: ResolvedPalette
) -> Shape:
    new_id = color_swap.get(shape.color)
    if new_id is None:
        return shape
    palette.rgba(new_id)  # raises PaletteError if new_id is not a known palette color
    return shape.model_copy(update={"color": new_id})


def _apply_scale_size(region_name: str, shape_index: int, shape: Shape, scale_size: Vec2) -> Shape:
    """Grow/shrink a rect/ellipse shape symmetrically about its centre.

    `size' = size + scale_size`, `at' = at - (scale_size // 2)` component-wise, using
    Python's floor-division `//` for both positive and negative `scale_size` — so a "+2
    width" edit grows 1px on each side, and an odd "+1 width" edit extends only the +x
    (right) / +y (bottom) side by the extra pixel, leaving the near side untouched. The
    revision system's `resize_region` operation depends on this exact convention: it must
    be able to compute the inverse offset without re-deriving it here.

    `PixelShape`/`LineShape` have no `size` and are returned unchanged.
    """
    if scale_size == (0, 0) or not isinstance(shape, RectShape | EllipseShape):
        return shape
    dw, dh = scale_size
    w, h = shape.size
    new_w, new_h = w + dw, h + dh
    if new_w < 1 or new_h < 1:
        raise RenderError(
            f"region {region_name!r} shape #{shape_index}: scale_size {scale_size} would "
            f"shrink size {shape.size} to ({new_w}, {new_h}); minimum is 1x1 per dimension"
        )
    ax, ay = shape.at
    new_at = (ax - dw // 2, ay - dh // 2)
    return shape.model_copy(update={"at": new_at, "size": (new_w, new_h)})


def plan_layers(
    doc: BaseAssetDoc,
    regions: Mapping[str, Region],
    anchors: Mapping[str, Vec2],
    transforms: Mapping[str, RegionTransform],
    palette: ResolvedPalette,
) -> list[LayerDraw]:
    """Build a deterministic draw plan for `regions`.

    `doc` is accepted per the documented interface but unused here: every piece of
    per-region context this function needs (anchors, merged transforms) is already
    resolved by the caller and passed in explicitly, so region subsets (e.g. only
    mirror-safe regions) can be planned without re-deriving anything from the full doc.

    Regions whose merged transform has `visible is False` are skipped. The result is
    sorted by `layer` ascending, then by region name for ties, so draw order is
    independent of `regions`' iteration order.
    """
    _ = doc
    layers: list[LayerDraw] = []
    for region_name, region in regions.items():
        transform = transforms.get(region_name, RegionTransform())
        if transform.visible is False:
            continue
        origin = anchor_world_pos(anchors, region.anchor, transform.offset)
        shapes: list[Shape] = []
        for shape_index, shape in enumerate(region.shapes):
            shape = _apply_color_swap(shape, transform.color_swap, palette)
            shape = _apply_scale_size(region_name, shape_index, shape, transform.scale_size)
            shapes.append(shape)
        layers.append(
            LayerDraw(region=region_name, layer=region.layer, origin=origin, shapes=tuple(shapes))
        )
    layers.sort(key=lambda draw: (draw.layer, draw.region))
    return layers


def composite(canvas_size: Vec2, layers: Sequence[LayerDraw], palette: ResolvedPalette) -> Canvas:
    """Draw `layers`, in list order, onto a fresh transparent canvas of `canvas_size`.

    Callers are responsible for ordering (`plan_layers` already sorts its output);
    `composite` never reorders. Out-of-canvas shapes clip silently via `Canvas.set_pixel`.
    """
    canvas = Canvas(*canvas_size)
    for layer in layers:
        for shape in layer.shapes:
            draw_shape(canvas, shape, layer.origin, palette.rgba(shape.color))
    return canvas
