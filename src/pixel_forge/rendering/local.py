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

import hashlib
from collections.abc import Mapping

import numpy as np
from numpy.typing import NDArray

from pixel_forge.animation.cycles import resolve_animation_frames
from pixel_forge.animation.resolver import (
    ResolvedFrame,
    frames_for,
    resolve_frames,
    resolve_sampled_frame,
)
from pixel_forge.animation.timeline import resample_frames
from pixel_forge.domain.geometry import mirror_anchors
from pixel_forge.domain.palette import (
    ResolvedPalette,
    palette_for_polish,
    resolve_palette,
)
from pixel_forge.errors import ForgeError
from pixel_forge.rendering.backend import RenderBackend, TileRenderBackend
from pixel_forge.rendering.canvas import Canvas
from pixel_forge.rendering.compositor import composite, composite_tagged, plan_layers
from pixel_forge.rendering.effects import polish_canvas
from pixel_forge.rendering.sheet import (
    build_variation_tiles,
    dominant_interior_color,
    shade_terrain_interior,
    variant_cell_id,
)
from pixel_forge.schemas.animation import FrameSpec
from pixel_forge.schemas.asset import SpriteAssetBase, TerrainAsset
from pixel_forge.schemas.style import ArtDirection


def _polish_palette(palette: ResolvedPalette) -> ResolvedPalette:
    """The palette the polish pass quantizes onto: the declared palette with
    `auto_ramp`/`derive_outline` forced on via `palette_for_polish`, so ramp
    tones and a derived `outline` colour exist as quantization targets.

    Composing still uses the caller's flat declared palette — shape colour ids
    resolve to identical RGBA either way, because expansion preserves every
    declared hex verbatim — so only the polish quantization targets grow.
    """
    return resolve_palette(palette_for_polish(palette.palette))


class LocalRenderBackend:
    name = "local"

    def render_frame(
        self,
        doc: SpriteAssetBase,
        frame: ResolvedFrame,
        palette: ResolvedPalette,
        *,
        art_direction: ArtDirection | None = None,
    ) -> Canvas:
        """Render `frame` as a `Canvas`. When `art_direction` is given, the flat
        composite is run through the deterministic render-polish pass
        (`rendering.effects.polish_canvas`) right before returning — plain Canvas in,
        polished Canvas out. `None` returns the raw flat composite.

        The polish pass receives the compositor's per-region ownership tags
        (`composite_tagged`), so the shading stage keys each pixel's light factor
        on its region's own local geometry (per-region form shading) rather than
        only the global sprite silhouette; AO/outline/ground shadow stay global.
        """
        if frame.mirrored_from is None:
            layers = plan_layers(doc, doc.regions, doc.anchors, frame.transforms, palette)
            if art_direction is not None:
                canvas, tags = composite_tagged(doc.asset.canvas, layers, palette)
            else:
                canvas = composite(doc.asset.canvas, layers, palette)
                tags = None
        else:
            canvas, tags = self._render_mirrored(
                doc, frame, palette, want_tags=art_direction is not None
            )
        if art_direction is not None:
            assert tags is not None
            canvas = polish_canvas(
                canvas, art_direction, _polish_palette(palette), region_tags=tags
            )
        return canvas

    def _render_mirrored(
        self,
        doc: SpriteAssetBase,
        frame: ResolvedFrame,
        palette: ResolvedPalette,
        *,
        want_tags: bool = False,
        source_frame: ResolvedFrame | None = None,
    ) -> tuple[Canvas, NDArray[np.int64] | None]:
        source_direction = frame.mirrored_from
        assert source_direction is not None
        if source_frame is None:
            source_frame = frames_for(doc, frame.animation, source_direction)[frame.index]

        safe_regions = {name: r for name, r in doc.regions.items() if r.mirror_safe}
        unsafe_regions = {name: r for name, r in doc.regions.items() if not r.mirror_safe}

        safe_layers = plan_layers(doc, safe_regions, doc.anchors, source_frame.transforms, palette)
        if want_tags:
            composed, safe_tags = composite_tagged(doc.asset.canvas, safe_layers, palette)
            mirrored = composed.mirror_x()
            safe_tags = np.fliplr(safe_tags)
        else:
            mirrored = composite(doc.asset.canvas, safe_layers, palette).mirror_x()
            safe_tags = None
        if not unsafe_regions:
            return mirrored, safe_tags

        unsafe_anchors = mirror_anchors(doc.anchors, doc.asset.canvas[0])
        unsafe_layers = plan_layers(
            doc, unsafe_regions, unsafe_anchors, frame.mirror_unsafe_transforms, palette
        )
        if want_tags:
            unsafe_canvas, unsafe_tags = composite_tagged(doc.asset.canvas, unsafe_layers, palette)
        else:
            unsafe_canvas = composite(doc.asset.canvas, unsafe_layers, palette)

        result = mirrored.copy()
        result.blit(unsafe_canvas, (0, 0))
        if not want_tags:
            return result, None
        # Stitch the ownership map: mirrored safe-region tags plus unsafe-region
        # tags stamped on top (tag ids offset past the safe region ids so the two
        # plans' indices never collide).
        assert safe_tags is not None
        tags = safe_tags.copy()
        visible = unsafe_tags >= 0
        tags[visible] = unsafe_tags[visible] + len(safe_layers)
        return result, tags

    def render_tile(
        self,
        doc: TerrainAsset,
        tile_id: str,
        palette: ResolvedPalette,
        *,
        art_direction: ArtDirection | None = None,
    ) -> Canvas:
        if tile_id not in doc.tiles:
            raise ForgeError(f"unknown tile id: {tile_id!r}; defined tiles: {sorted(doc.tiles)}")
        tile = doc.tiles[tile_id]
        layers = plan_layers(doc, tile.regions, tile.anchors, {}, palette)
        if art_direction is not None:
            canvas, tags = composite_tagged(tile.size, layers, palette)
            polish_palette = _polish_palette(palette)
            canvas = polish_canvas(
                canvas, _terrain_polish_direction(art_direction), polish_palette, region_tags=tags
            )
            canvas = shade_terrain_interior(canvas, polish_palette, seed=_shade_seed(tile_id))
            canvas = tint_tile_ring(canvas, polish_palette)
        else:
            canvas = composite(tile.size, layers, palette)
        return canvas


def render_asset_frames(
    doc: SpriteAssetBase,
    backend: RenderBackend | None = None,
    *,
    art_direction: ArtDirection | None = None,
    ease_samples: int = 4,
) -> dict[tuple[str, str, int] | tuple[str, str, int, int], Canvas]:
    """Render every resolved frame of `doc`.

    Keys are ``(animation, direction, index)`` for every authored frame, in the
    same order `animation.resolve_frames` emits them (a plain dict — insertion
    order is the contract). When an animation track declares per-frame
    ``easing`` or ``hold`` on any frame, eased sub-frames are additionally
    rendered under ``(animation, direction, authored_index, sub)`` for ``sub``
    in ``1..ease_samples-1``, sampled from the track's timeline via
    `animation.timeline.resample_frames` — so a spec setting ``easing`` or
    ``hold`` visibly reshapes the rendered intermediate motion instead of being
    inert metadata. The authored keys are the timeline pose at each frame's
    start (byte-identical to un-eased rendering), sub-frames carry a fraction of
    the authored duration (``duration_ms // ease_samples``, remainder on the
    last) and never re-fire events. Tracks without easing/hold emit exactly the
    authored frames, keeping every existing spec and golden byte-identical.

    `art_direction` is only honoured for `LocalRenderBackend` — the shape-DSL
    backend whose output is flat art that benefits from the polish pass.
    `ExternalFrameBackend` (and any future backend) supplies final authored
    pixels and is never post-processed (and has no timeline to sample, so eased
    sub-frames are only produced for `LocalRenderBackend`): passing
    `art_direction` there is a no-op."""
    if ease_samples < 1:
        raise ForgeError(f"render_asset_frames: ease_samples must be >= 1, got {ease_samples}")
    backend = backend or LocalRenderBackend()
    palette = resolve_palette(doc.palette)
    resolved = resolve_frames(doc)
    frames: dict[tuple[str, str, int] | tuple[str, str, int, int], Canvas] = {}
    for frame in resolved:
        key = (frame.animation, frame.direction, frame.index)
        if art_direction is not None and isinstance(backend, LocalRenderBackend):
            frames[key] = backend.render_frame(doc, frame, palette, art_direction=art_direction)
        else:
            frames[key] = backend.render_frame(doc, frame, palette)
    _render_eased_subframes(
        doc,
        backend,
        palette,
        resolved,
        frames,
        art_direction=art_direction,
        ease_samples=ease_samples,
    )
    return frames


def _render_eased_subframes(
    doc: SpriteAssetBase,
    backend: RenderBackend,
    palette: ResolvedPalette,
    resolved: list[ResolvedFrame],
    frames: dict[tuple[str, str, int] | tuple[str, str, int, int], Canvas],
    *,
    art_direction: ArtDirection | None,
    ease_samples: int,
) -> None:
    """Easing/hold end-to-end wiring: densify every track that declares per-frame
    easing or hold and render the sampled sub-frames into `frames` (mutated in
    place). Tracks without easing/hold — and non-`LocalRenderBackend` backends,
    which have no timeline to sample — are left untouched."""
    if ease_samples < 2 or not isinstance(backend, LocalRenderBackend):
        return
    groups: dict[tuple[str, str], list[ResolvedFrame]] = {}
    for frame in resolved:
        groups.setdefault((frame.animation, frame.direction), []).append(frame)
    for (animation, direction), group in groups.items():
        anim_spec = doc.animations.get(animation)
        if anim_spec is None:
            continue
        frame_specs = resolve_animation_frames(doc, anim_spec)
        if not any(spec.easing is not None or spec.hold for spec in frame_specs):
            continue
        group.sort(key=lambda f: f.index)
        resampled = resample_frames(frame_specs, ease_samples)
        for i, authored in enumerate(group):
            for k in range(1, ease_samples):
                spec = resampled[i * ease_samples + k]
                sub = resolve_sampled_frame(
                    doc, animation, direction, spec, index=i * ease_samples + k
                )
                key = (animation, direction, authored.index, k)
                frames[key] = _render_subframe(
                    doc, backend, sub, spec, palette, art_direction=art_direction
                )


def _render_subframe(
    doc: SpriteAssetBase,
    backend: LocalRenderBackend,
    sub: ResolvedFrame,
    spec: FrameSpec,
    palette: ResolvedPalette,
    *,
    art_direction: ArtDirection | None,
) -> Canvas:
    """Render one eased sub-frame. Mirrored sub-frames render the source
    direction's matching sub-frame (same eased pose, same index) so the
    `_render_mirrored` source lookup stays consistent; unsafe regions still use
    this frame's mirror-aware `mirror_unsafe_transforms`."""
    if sub.mirrored_from is None:
        return backend.render_frame(doc, sub, palette, art_direction=art_direction)
    source = resolve_sampled_frame(
        doc, sub.animation, sub.mirrored_from, spec, index=sub.index
    )
    canvas, tags = backend._render_mirrored(
        doc, sub, palette, want_tags=art_direction is not None, source_frame=source
    )
    if art_direction is not None:
        assert tags is not None
        canvas = polish_canvas(
            canvas, art_direction, _polish_palette(palette), region_tags=tags
        )
    return canvas


def render_terrain_tiles(
    doc: TerrainAsset,
    backend: TileRenderBackend | None = None,
    *,
    art_direction: ArtDirection | None = None,
) -> dict[str, Canvas]:
    """Render every tile of `doc`, keyed by tile id in sorted order (matching
    `animation.resolve_terrain_frames`'s convention for static tiles).

    `art_direction` is only honoured for `LocalRenderBackend` (see `render_asset_frames`)."""
    backend = backend or LocalRenderBackend()
    palette = resolve_palette(doc.palette)
    tiles: dict[str, Canvas] = {}
    for tile_id in sorted(doc.tiles):
        if art_direction is not None and isinstance(backend, LocalRenderBackend):
            tiles[tile_id] = backend.render_tile(doc, tile_id, palette, art_direction=art_direction)
        else:
            tiles[tile_id] = backend.render_tile(doc, tile_id, palette)
    return tiles


# --- terrain-specific polish: flat interiors, tone-matched seam rings ----------------


def _terrain_polish_direction(direction: ArtDirection) -> ArtDirection:
    """`direction` with every per-tile bevel stage forced off.

    A terrain tile's silhouette *is* the tile, so the sprite-oriented polish
    stages (directional edge-band shading, inner ambient occlusion, ink
    outline, contact shadow) would bevel the tile and crush its shared grout
    ring to near-black ink — the exact failure that makes a field read as a
    grid of raised 16px blocks. `render_tile` forces these four knobs off no
    matter what `art_direction` the caller passed (the pipeline passes
    `ArtDirection.terrain_default()`, which already zeroes them), then applies
    the tone-matched ring pass itself. The light *direction* is preserved,
    so a global light treatment stays consistent across the whole field.
    """
    return direction.model_copy(
        update={
            "shadow_strength": 0,
            "highlight_strength": 0,
            "ambient_occlusion_strength": 0,
            "outline_width": 0,
            "ground_shadow_enabled": False,
        }
    )


def tint_tile_ring(canvas: Canvas, palette: ResolvedPalette) -> Canvas:
    """Tone-match a terrain tile's 1px border ring to its dominant interior
    colour — the seam treatment that makes a tiled field read as continuous
    ground instead of a 16px lattice.

    The dominant interior colour (most common opaque interior pixel, ring
    excluded) becomes the ring colour **exactly** (mapped through
    `palette.nearest` so it is an exact palette colour by construction; after
    the polish pass the dominant interior pixel already *is* a palette colour,
    so this is identity). When two identical tiles abut, the boundary pixels
    are the same colour as the neighbouring interiors on both sides — CIE L*
    delta 0 from the interior — so the grid is invisible. Material changes are
    handled by the transition tiles' organic interior fringes, not by a dark
    outline: this is the reference's edge-wrap look under the engine's
    uniform-ring seam contract.

    The border ring is the tile's seam contract: the whole ring becomes one
    uniform colour per tile, so every tile's N/S/E/W edge still matches
    itself (self-seams stay zero). Generated variants never touch the ring, so
    every variant keeps its base tile's seams byte-for-byte.

    Pure function of the canvas + palette, deterministic, never mutates
    `canvas`. Ring pixels that are transparent (partially-drawn tiles) are
    left untouched.
    """
    out = canvas.copy()
    arr = out.array
    dominant = dominant_interior_color(canvas)
    if dominant is None:
        return out
    color_id = palette.nearest((*dominant, 255))
    tone = palette.rgba(color_id)[:3]
    ring = np.zeros(arr.shape[:2], dtype=bool)
    ring[0, :] = True
    ring[-1, :] = True
    ring[:, 0] = True
    ring[:, -1] = True
    ring &= arr[..., 3] != 0
    arr[ring, 0] = tone[0]
    arr[ring, 1] = tone[1]
    arr[ring, 2] = tone[2]
    return out


def _variant_seed(tile_id: str) -> int:
    """Stable per-tile seed for `build_variation_tiles`' interior scatter:
    a pure function of the tile id, so two different tiles get different
    scatter patterns and repeat renders are byte-identical."""
    return int(hashlib.sha256(tile_id.encode("utf-8")).hexdigest()[:8], 16)


def _shade_seed(tile_id: str) -> int:
    """Stable per-tile seed for `shade_terrain_interior`'s interior tone
    scatter — deliberately distinct from `_variant_seed`, so a tile's shade
    pattern and its variant scatter never correlate."""
    return int(hashlib.sha256(f"shade:{tile_id}".encode()).hexdigest()[:8], 16)


def expand_terrain_variants(
    doc: TerrainAsset,
    tiles: Mapping[str, Canvas],
    palette: ResolvedPalette,
) -> dict[str, Canvas]:
    """Expand each tile's `TileSpec.variations` into atlas cells.

    Returns the full atlas cell map: every base tile under its own id plus
    `variations - 1` generated variant cells per tile under `{tile_id}.v{i}`
    (i = 1..variations-1), so a spec declaring `variations: 4` emits exactly
    4 distinct cells. Variant cells are generated by `build_variation_tiles`
    with the tile id's stable seed and the caller's palette (the pipeline
    passes the polish-expanded palette, so variants scatter the material's
    own dark/light ramp tones — grass tufts on grass, pebbles on dirt — never
    a palette swap). The 1px border ring is never touched, so every variant
    keeps its base tile's seams byte-for-byte.

    Animation-frame tiles (members of any `animated_tiles` frames list) are
    never expanded: their cells are Godot animation-strip frames, not
    standalone tiles. Deterministic — a pure function of `doc`, `tiles` and
    `palette`.
    """
    frame_ids = {tid for spec in doc.animated_tiles.values() for tid in spec.frames}
    cells: dict[str, Canvas] = {}
    for tile_id in sorted(tiles):
        cells[tile_id] = tiles[tile_id]
        spec = doc.tiles.get(tile_id)
        if spec is None or spec.variations <= 1 or tile_id in frame_ids:
            continue
        variants = build_variation_tiles(
            tiles[tile_id],
            palette,
            spec.variations - 1,
            seed=_variant_seed(tile_id),
        )
        for i, variant in enumerate(variants, start=1):
            cells[variant_cell_id(tile_id, i)] = variant
    return cells
