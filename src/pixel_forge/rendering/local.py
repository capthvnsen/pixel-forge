"""`LocalRenderBackend`: the MVP `RenderBackend`/`TileRenderBackend` implementation.

A non-mirrored direction composites `doc.regions` directly: each region's origin is
`anchors[region.anchor] + transform.offset`.

A mirrored direction (`frame.mirrored_from is not None`) renders the *source*
direction's own frame (same animation/index, source direction's own transforms),
restricted to mirror-safe regions, then flips the whole canvas with `Canvas.mirror_x()`.
Flipping the finished raster is exactly equivalent to mirroring every mirror-safe
shape's coordinates individually: `domain.geometry.mirror_point_x` (`x' = canvas_width
- 1 - x`) is `Canvas.mirror_x`'s exact inverse for both even and odd widths (see that
module's docstring), so there is no need to re-derive mirrored anchors for those
regions.

Regions with `mirror_safe = False` are excluded from that flip and instead composited
on top, drawn unflipped but *attached to the mirrored body*: their origin uses
`domain.geometry.mirror_anchors` (each anchor's x mirrored about the canvas centre),
not the raw, unmirrored anchor — otherwise a hand/insignia/etc. anchored on the source
side stays frozen on the wrong side once the body flips under it. Their per-region
transform is `frame.mirror_unsafe_transforms`, not `frame.transforms`:
`animation.resolve_frames` computes it separately so a `direction_overrides` entry
*inherited* from the mirror source (no override authored for this direction) has its
offset's x component negated, while one *authored* explicitly for this direction is
used exactly as written — the author was describing this direction, not the source.
A frame-level (animation) transform is never mirrored either way: direction-specific
motion is deliberately not carried over onto an unsafe region.

Everything clips to `doc.asset.canvas` for free via `Canvas.set_pixel`'s out-of-bounds
no-op; an off-canvas region is never an error.
"""

from __future__ import annotations

from pixel_forge.animation.resolver import ResolvedFrame, frames_for, resolve_frames
from pixel_forge.domain.geometry import mirror_anchors
from pixel_forge.domain.palette import ResolvedPalette, resolve_palette
from pixel_forge.errors import ForgeError
from pixel_forge.rendering.backend import RenderBackend, TileRenderBackend
from pixel_forge.rendering.canvas import Canvas
from pixel_forge.rendering.compositor import composite, plan_layers
from pixel_forge.schemas.asset import SpriteAssetBase, TerrainAsset


class LocalRenderBackend:
    name = "local"

    def render_frame(
        self, doc: SpriteAssetBase, frame: ResolvedFrame, palette: ResolvedPalette
    ) -> Canvas:
        if frame.mirrored_from is None:
            layers = plan_layers(doc, doc.regions, doc.anchors, frame.transforms, palette)
            return composite(doc.asset.canvas, layers, palette)
        return self._render_mirrored(doc, frame, palette)

    def _render_mirrored(
        self, doc: SpriteAssetBase, frame: ResolvedFrame, palette: ResolvedPalette
    ) -> Canvas:
        source_direction = frame.mirrored_from
        assert source_direction is not None
        source_frame = frames_for(doc, frame.animation, source_direction)[frame.index]

        safe_regions = {name: r for name, r in doc.regions.items() if r.mirror_safe}
        unsafe_regions = {name: r for name, r in doc.regions.items() if not r.mirror_safe}

        safe_layers = plan_layers(doc, safe_regions, doc.anchors, source_frame.transforms, palette)
        mirrored = composite(doc.asset.canvas, safe_layers, palette).mirror_x()
        if not unsafe_regions:
            return mirrored

        unsafe_anchors = mirror_anchors(doc.anchors, doc.asset.canvas[0])
        unsafe_layers = plan_layers(
            doc, unsafe_regions, unsafe_anchors, frame.mirror_unsafe_transforms, palette
        )
        unsafe_canvas = composite(doc.asset.canvas, unsafe_layers, palette)

        result = mirrored.copy()
        result.blit(unsafe_canvas, (0, 0))
        return result

    def render_tile(self, doc: TerrainAsset, tile_id: str, palette: ResolvedPalette) -> Canvas:
        if tile_id not in doc.tiles:
            raise ForgeError(f"unknown tile id: {tile_id!r}; defined tiles: {sorted(doc.tiles)}")
        tile = doc.tiles[tile_id]
        layers = plan_layers(doc, tile.regions, tile.anchors, {}, palette)
        return composite(tile.size, layers, palette)


def render_asset_frames(
    doc: SpriteAssetBase, backend: RenderBackend | None = None
) -> dict[tuple[str, str, int], Canvas]:
    """Render every resolved frame of `doc`, keyed `(animation, direction, index)` in the
    same order `animation.resolve_frames` emits them (a plain dict — insertion order is
    the contract)."""
    backend = backend or LocalRenderBackend()
    palette = resolve_palette(doc.palette)
    frames: dict[tuple[str, str, int], Canvas] = {}
    for frame in resolve_frames(doc):
        frames[(frame.animation, frame.direction, frame.index)] = backend.render_frame(
            doc, frame, palette
        )
    return frames


def render_terrain_tiles(
    doc: TerrainAsset, backend: TileRenderBackend | None = None
) -> dict[str, Canvas]:
    """Render every tile of `doc`, keyed by tile id in sorted order (matching
    `animation.resolve_terrain_frames`'s convention for static tiles)."""
    backend = backend or LocalRenderBackend()
    palette = resolve_palette(doc.palette)
    return {tile_id: backend.render_tile(doc, tile_id, palette) for tile_id in sorted(doc.tiles)}
