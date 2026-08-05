"""The render backend seam.

`RenderBackend` and `TileRenderBackend` are the one point of contact between the pure
domain (schemas + resolved animation state) and whatever actually turns a spec into
pixels. The MVP's only implementation is `LocalRenderBackend` (`rendering/local.py`),
which draws the shape DSL deterministically with `rendering.compositor`. Nothing else
in the domain knows or cares that this is how it happens: `api.py` and the validators
call these Protocols, never `LocalRenderBackend` directly.

This is deliberately the seam where a future generative-image or vision-model backend
could be plugged in instead — e.g. one that asks an external model to paint a region,
or to critique/repair pixels against the spec — without any change to `schemas`,
`animation`, or the callers of `render_asset_frames`/`render_terrain_tiles`.

Any backend satisfying these Protocols, present or future, must satisfy the same
determinism contract: for a given `(doc, frame, palette)` or `(doc, tile_id, palette)`,
`render_frame`/`render_tile` must return a byte-identical `Canvas` every time it is
called. No randomness, no timestamps, no network/model variance leaking into pixels —
if a backend samples a model, it must cache/pin the result so repeat renders agree.
"""

from __future__ import annotations

from typing import Protocol

from pixel_forge.animation.resolver import ResolvedFrame
from pixel_forge.domain.palette import ResolvedPalette
from pixel_forge.rendering.canvas import Canvas
from pixel_forge.schemas.asset import SpriteAssetBase, TerrainAsset


class RenderBackend(Protocol):
    name: str

    def render_frame(
        self, doc: SpriteAssetBase, frame: ResolvedFrame, palette: ResolvedPalette
    ) -> Canvas: ...


class TileRenderBackend(Protocol):
    name: str

    def render_tile(self, doc: TerrainAsset, tile_id: str, palette: ResolvedPalette) -> Canvas: ...
