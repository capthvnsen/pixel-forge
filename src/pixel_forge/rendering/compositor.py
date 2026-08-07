"""Layer stack -> composed frame.

`plan_layers` turns a region map + resolved anchors + per-region transforms into an
ordered, transform-adjusted draw plan (`LayerDraw`). `composite` draws that plan onto a
fresh transparent canvas. Splitting the two lets `rendering.local` reuse `plan_layers`
on region subsets (mirror-safe vs mirror-unsafe) before compositing them separately.

`scale_size` only ever grows/shrinks a `rect`/`ellipse`'s `size`; it is ignored for
`bitmap` shapes (and `pixel`/`line`, which have no `size` either). Resampling hand-drawn
pixel art is not a size delta: silently nearest-scaling it would be worse than doing
nothing, so bitmaps stay their authored size and any resizing is a re-authoring problem.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from pixel_forge.domain.geometry import anchor_world_pos
from pixel_forge.domain.palette import ResolvedPalette
from pixel_forge.errors import RenderError
from pixel_forge.rendering.canvas import Canvas, Vec2
from pixel_forge.rendering.shapes import draw_bitmap, draw_shape
from pixel_forge.schemas.asset import BaseAssetDoc
from pixel_forge.schemas.common import (
    BitmapShape,
    EllipseShape,
    RectShape,
    Region,
    RegionTransform,
    Shape,
)


@dataclass(frozen=True)
class LayerDraw:
    region: str
    layer: int
    origin: Vec2  # resolved anchor world position + transform offset
    shapes: tuple[Shape, ...]  # transform-adjusted shapes


def _apply_color_swap(
    shape: Shape, color_swap: Mapping[str, str], palette: ResolvedPalette
) -> Shape:
    """Rewrite a shape's colour references per `color_swap` (old palette id -> new).

    A `BitmapShape` has no `.color`; it carries colour through `key`'s values instead, so
    every value is looked up individually and rewritten in place. Any single-colour shape
    just swaps `.color`. Palette validation of unknown swap targets happens later, when the
    swapped colour id is actually resolved to RGBA (in `composite`) — that way an unused
    key entry pointing at a bad swap target still surfaces as a `PaletteError`.
    """
    if isinstance(shape, BitmapShape):
        if not color_swap:
            return shape
        new_key = {char: color_swap.get(color_id, color_id) for char, color_id in shape.key.items()}
        return shape.model_copy(update={"key": new_key})
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

    `PixelShape`/`LineShape`/`BitmapShape` have no `size` and are returned unchanged: a
    bitmap's dimensions come from its `rows`, and resampling art is not a size delta.
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
            if isinstance(shape, BitmapShape):
                colors = {char: palette.rgba(color_id) for char, color_id in shape.key.items()}
                draw_bitmap(canvas, shape, layer.origin, colors)
            else:
                draw_shape(canvas, shape, layer.origin, palette.rgba(shape.color))
    return canvas


def composite_tagged(
    canvas_size: Vec2, layers: Sequence[LayerDraw], palette: ResolvedPalette
) -> tuple[Canvas, NDArray[np.int64]]:
    """Composite `layers` and return `(canvas, tags)` where `tags[y, x]` is the index
    into `layers` of the region that drew that pixel — the TOPMOST region at that
    pixel (later layers overwrite earlier tags, exactly matching blit order) — or -1
    for transparent pixels.

    The returned canvas is byte-identical to `composite(canvas_size, layers, palette)`
    (each layer is drawn onto its own scratch canvas and blitted in the same order),
    so callers may use this function wherever they need per-region ownership without
    changing the composed pixels. Deterministic: pure integer rasterisation, layer
    order fixed by the plan. The tags are what the render-polish pass uses to shade
    each region against its OWN local geometry (per-region form shading) instead of
    only the global sprite silhouette.
    """
    canvas = Canvas(*canvas_size)
    h, w = canvas.height, canvas.width
    tags: NDArray[np.int64] = np.full((h, w), -1, dtype=np.int64)
    for index, layer in enumerate(layers):
        scratch = composite(canvas_size, [layer], palette)
        mask = scratch.array[..., 3] != 0
        tags[mask] = index
        canvas.blit(scratch, (0, 0))
    return canvas, tags
