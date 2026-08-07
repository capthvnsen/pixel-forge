"""Unit tests for the render-polish post-processing pass (`rendering.effects`).

Covers the six required properties of the pass:

(a) a flat single-colour rect gains >= 2 distinct tones (shading ramps);
(b) the silhouette gains an ink outline ring on its outer edge;
(c) running the pass twice on the same canvas yields byte-identical arrays
    (determinism — and the pass is pure integer arithmetic, so this holds across
    platforms);
(d) transparent pixels stay transparent (the pass never fills transparency —
    the ground shadow is the *only* intentional new-opaque region, and only below
    the sprite's feet);
(e) alpha stays strictly 0 or 255 everywhere — no semi-transparent pixels, ever;
(f) every pixel the pass writes is an approved palette colour (the pass
    quantizes back onto the palette after all four stages, so PIX003/PIX004 can
    never fire on polished output).

Plus integration coverage for the `LocalRenderBackend` wiring: `render_frame` /
`render_tile` / `render_asset_frames` produce raw flat output when no art
direction is passed (existing callers unchanged) and polished, palette-clean
output when one is.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import NDArray

from pixel_forge.animation.resolver import resolve_frames
from pixel_forge.domain.palette import (
    ResolvedPalette,
    expand_palette,
    palette_for_polish,
    resolve_palette,
)
from pixel_forge.rendering.canvas import Canvas
from pixel_forge.rendering.compositor import composite, composite_tagged, plan_layers
from pixel_forge.rendering.effects import _run_distances, polish_canvas
from pixel_forge.rendering.local import LocalRenderBackend
from pixel_forge.schemas import Palette, PaletteColor, parse_asset_doc
from pixel_forge.schemas.style import ArtDirection

OUTLINE = (26, 26, 31, 255)  # the declared outline colour #1a1a1f
RED = (200, 60, 40, 255)  # hex #c83c28


def _rgb3(rgba: tuple[int, int, int, int]) -> tuple[int, int, int]:
    return (rgba[0], rgba[1], rgba[2])


def _polish_palette_for(doc: Any) -> ResolvedPalette:
    """The expanded polish palette for a doc: auto-ramp + derived outline forced
    on, exactly as `LocalRenderBackend` builds it via `palette_for_polish`."""
    return resolve_palette(palette_for_polish(doc.palette))


def _polish_palette() -> ResolvedPalette:
    """The expanded palette the pass quantizes onto: RED's 3-step auto-ramp plus
    an explicitly declared outline colour (#1a1a1f, kept flat), so the inked
    silhouette ring survives quantization verbatim. `palette_for_polish`'s
    derived-outline path is exercised by the LocalRenderBackend wiring tests."""
    palette = Palette(
        id="p",
        colors=[
            PaletteColor(id="red", hex="#c83c28"),
            PaletteColor(id="outline", hex="#1a1a1f", role="outline", ramp_steps=1),
        ],
        auto_ramp=True,
        derive_outline=True,
    )
    return resolve_palette(expand_palette(palette))


def _rect_canvas(
    width: int = 10, height: int = 10, at: tuple[int, int] = (2, 2), size: tuple[int, int] = (6, 6)
) -> Canvas:
    canvas = Canvas(width, height)
    x0, y0 = at
    sw, sh = size
    for y in range(y0, y0 + sh):
        for x in range(x0, x0 + sw):
            canvas.set_pixel(x, y, RED)
    return canvas


def _sprite_doc() -> Any:
    return parse_asset_doc(
        {
            "schema_version": 1,
            "asset": {"id": "x", "type": "character", "canvas": [8, 8]},
            "palette": {
                "id": "p",
                "colors": [
                    {"id": "red", "hex": "#c83232"},
                    {"id": "blue", "hex": "#3264c8"},
                    {"id": "outline", "hex": "#1a1a1f", "role": "outline"},
                ],
            },
            "directions": ["south"],
            "anchors": {"root": [0, 0]},
            "regions": {
                "body": {
                    "anchor": "root",
                    "layer": 0,
                    "shapes": [{"op": "rect", "color": "red", "at": [1, 1], "size": [4, 4]}],
                }
            },
            "direction_overrides": {},
            "animations": {
                "idle": {
                    "loop": True,
                    "frames": [{"duration_ms": 100, "events": [], "transforms": {}}],
                }
            },
            "export": {},
            "validation": {},
        }
    )


def _mirror_doc() -> Any:
    """A two-direction sprite whose east frames mirror west (like engineer's
    `mirror: {east: west}`); the body rect is off-centre so the two directions
    render genuinely different canvases through the mirror path. The body rect
    is 32x32 so the size-adaptive shading radius keeps its full configured
    value and the world-fixed light direction is actually visible."""
    return parse_asset_doc(
        {
            "schema_version": 1,
            "asset": {"id": "m", "type": "character", "canvas": [40, 40]},
            "palette": {
                "id": "p",
                "colors": [
                    {"id": "red", "hex": "#c83232"},
                    {"id": "outline", "hex": "#1a1a1f", "role": "outline"},
                ],
            },
            "directions": ["west", "east"],
            "mirror": {"east": "west"},
            "anchors": {"root": [0, 0]},
            "regions": {
                "body": {
                    "anchor": "root",
                    "layer": 0,
                    "shapes": [{"op": "rect", "color": "red", "at": [4, 4], "size": [32, 32]}],
                }
            },
            "direction_overrides": {},
            "animations": {
                "idle": {
                    "loop": True,
                    "frames": [{"duration_ms": 100, "events": [], "transforms": {}}],
                }
            },
            "export": {},
            "validation": {},
        }
    )


# --- (a) shading: a flat rect gains multiple distinct tones ------------------------------------


def test_flat_rect_gains_multiple_distinct_tones() -> None:
    out = polish_canvas(_rect_canvas(), ArtDirection.default(), _polish_palette())
    tones = out.colors()
    assert len(tones) >= 2
    # The original flat colour survives as the mid tone somewhere.
    assert RED in tones


def test_flat_rect_gains_both_highlight_and_shadow_tones() -> None:
    # 32x32 rect: large enough that the size-adaptive shading radius keeps its
    # full configured value, so both bands are visible on one sprite.
    out = polish_canvas(
        _rect_canvas(width=36, height=36, at=(2, 2), size=(32, 32)),
        ArtDirection.default(),
        _polish_palette(),
    )
    r, g, b, _a = out.get_pixel(4, 4)  # near the top-left (lit) corner, interior
    sr, sg, sb, _a2 = out.get_pixel(31, 31)  # near the bottom-right (shadow) corner, interior
    assert (r + g + b) > (sr + sg + sb)
    assert r >= RED[0] and sr <= RED[0]


# --- (b) ink outline ---------------------------------------------------------------------------


def test_silhouette_gains_ink_outline_ring() -> None:
    out = polish_canvas(_rect_canvas(), ArtDirection.default(), _polish_palette())
    # Every outer boundary pixel of the rect becomes the ink outline colour.
    for x, y in [(2, 2), (2, 7), (7, 2), (7, 7), (4, 2)]:
        assert out.get_pixel(x, y) == OUTLINE, (x, y)
    # An interior pixel is not inked.
    assert out.get_pixel(4, 4)[:3] != OUTLINE[:3]
    # Pixels outside the silhouette stay transparent (row 0 is above the sprite;
    # the ground shadow only ever extends *below* the feet).
    assert out.get_pixel(0, 0) == (0, 0, 0, 0)
    assert out.get_pixel(9, 0) == (0, 0, 0, 0)
    assert out.get_pixel(0, 9) == (0, 0, 0, 0)


def test_outline_width_controls_ring_count() -> None:
    two = ArtDirection.default().model_copy(update={"outline_width": 2})
    out = polish_canvas(_rect_canvas(), two, _polish_palette())
    assert out.get_pixel(2, 2) == OUTLINE  # ring 1
    assert out.get_pixel(3, 3) == OUTLINE  # ring 2
    assert out.get_pixel(4, 4)[:3] != OUTLINE[:3]  # interior stays shaded, not inked

    zero = ArtDirection.default().model_copy(update={"outline_width": 0})
    none = polish_canvas(_rect_canvas(), zero, _polish_palette())
    assert none.get_pixel(2, 2)[:3] != OUTLINE[:3]


# --- (c) determinism ----------------------------------------------------------------------------


def test_pass_is_deterministic() -> None:
    canvas = _rect_canvas(width=12, height=12, size=(8, 8))
    first = polish_canvas(canvas, ArtDirection.default(), _polish_palette())
    second = polish_canvas(canvas, ArtDirection.default(), _polish_palette())
    assert np.array_equal(first.array, second.array)


def test_polished_render_path_is_deterministic() -> None:
    doc = _sprite_doc()
    palette = resolve_palette(doc.palette)
    frame = resolve_frames(doc)[0]
    backend = LocalRenderBackend()
    direction = ArtDirection.default()
    first = backend.render_frame(doc, frame, palette, art_direction=direction)
    second = backend.render_frame(doc, frame, palette, art_direction=direction)
    assert first.equals(second)


# --- (d) transparency is preserved ---------------------------------------------------------------


def test_transparent_pixels_stay_transparent() -> None:
    direction = ArtDirection.default().model_copy(update={"ground_shadow_enabled": False})
    canvas = _rect_canvas()
    out = polish_canvas(canvas, direction, _polish_palette())
    was_transparent = canvas.array[..., 3] == 0
    assert np.all(out.array[was_transparent, 3] == 0)


def test_ground_shadow_is_the_only_newly_opaque_region() -> None:
    # Rect spans rows 2..7 on a 10x10 canvas, so the shadow may only appear in
    # rows 8 and 9 (below the feet) and nowhere else.
    canvas = _rect_canvas()
    out = polish_canvas(canvas, ArtDirection.default(), _polish_palette())
    new_opaque = (canvas.array[..., 3] == 0) & (out.array[..., 3] != 0)
    ys, xs = np.nonzero(new_opaque)
    assert len(ys) > 0, "expected a contact shadow under the sprite"
    assert set(ys.tolist()) <= {8, 9}
    # The shadow hugs the sprite's horizontal span, widening by at most ~1px per
    # side (the ellipse-ish footprint rounds outward from the feet's centre).
    assert set(xs.tolist()) <= set(range(1, 9))
    # Everything outside the shadow region that was transparent stays transparent.
    outside_shadow = (canvas.array[..., 3] == 0) & ~new_opaque
    assert np.all(out.array[outside_shadow, 3] == 0)


def test_ground_shadow_can_be_disabled() -> None:
    direction = ArtDirection.default().model_copy(update={"ground_shadow_enabled": False})
    canvas = _rect_canvas()
    out = polish_canvas(canvas, direction, _polish_palette())
    assert np.all((out.array[..., 3] == 0) == (canvas.array[..., 3] == 0))


# --- (e) alpha is strictly binary ----------------------------------------------------------------


def test_alpha_remains_strictly_binary_everywhere() -> None:
    canvas = _rect_canvas(width=12, height=12, at=(1, 1), size=(9, 9))
    out = polish_canvas(canvas, ArtDirection.default(), _polish_palette())
    alpha = out.array[..., 3]
    assert np.all((alpha == 0) | (alpha == 255))


# --- (f) palette discipline: every written pixel is an approved colour ----------------


def test_every_opaque_pixel_is_an_approved_palette_colour() -> None:
    # The regression the polish pass originally shipped with: shading blends,
    # the ink outline, and the ground shadow wrote non-palette colours that
    # PIX003/PIX004 then flagged. After the final quantization every opaque
    # pixel must be exactly an approved palette colour. 32x32 rect so the full
    # shading radius renders both ramp tones.
    canvas = _rect_canvas(width=36, height=36, at=(2, 2), size=(32, 32))
    out = polish_canvas(canvas, ArtDirection.default(), _polish_palette())
    palette = _polish_palette()
    opaque = out.array[..., 3] != 0
    for y in range(out.height):
        for x in range(out.width):
            if not opaque[y, x]:
                continue
            rgba = out.get_pixel(x, y)
            assert palette.contains_rgba(rgba), f"({x},{y}) -> {rgba} not in palette"
    # And the polish is still visible: multiple tones + an inked silhouette.
    assert len(out.colors()) >= 3
    assert out.get_pixel(2, 2) == OUTLINE


# --- light direction ----------------------------------------------------------------------------


def test_light_comes_from_the_top_left() -> None:
    # 32x32 rect on a 36x36 canvas: the interior pixel near the top edge must be
    # brighter than the symmetric interior pixel near the bottom edge, and the
    # interior pixel near the left edge brighter than the one near the right edge
    # (radius stays 4 at this size, so the bands are visible).
    canvas = _rect_canvas(width=36, height=36, at=(2, 2), size=(32, 32))
    out = polish_canvas(canvas, ArtDirection.default(), _polish_palette())
    top = out.get_pixel(17, 4)
    bottom = out.get_pixel(17, 32)
    assert sum(top[:3]) > sum(bottom[:3])
    left = out.get_pixel(4, 17)
    right = out.get_pixel(32, 17)
    assert sum(left[:3]) > sum(right[:3])


def test_highlight_ramp_tone_appears_in_polished_frame() -> None:
    # Regression for the render-polish highlight bug: shading used to
    # interpolate highlights toward white and the final quantization snapped
    # them back onto the base colour, so the derived `_light` ramp tone never
    # appeared in output. The tone now maps directly onto the material ramp, so
    # the light tone must be present in a polished frame (32x32 rect keeps the
    # full shading radius, so the band actually renders).
    out = polish_canvas(
        _rect_canvas(width=36, height=36, at=(2, 2), size=(32, 32)),
        ArtDirection.default(),
        _polish_palette(),
    )
    red_light = _polish_palette().rgba("red_light")
    assert red_light in out.colors()
    # And the deep shadow tone too: both sides of the ramp are reachable.
    red_shadow = _polish_palette().rgba("red_shadow")
    assert red_shadow in out.colors()


def test_five_step_ramp_reaches_deepest_and_lightest_steps() -> None:
    # A 5-step auto-ramp: the tone index stretches across the whole ramp, so a
    # 3-tone light factor still reaches the `_shadow` and `_bright` extremes.
    palette = Palette(
        id="p",
        colors=[
            PaletteColor(id="steel", hex="#64718c", ramp_steps=5),
            PaletteColor(id="outline", hex="#1a1a1f", role="outline", ramp_steps=1),
        ],
        auto_ramp=True,
        derive_outline=True,
    )
    expanded = resolve_palette(expand_palette(palette))
    canvas = Canvas(36, 36)
    steel = expanded.rgba("steel")
    for y in range(2, 34):
        for x in range(2, 34):
            canvas.set_pixel(x, y, steel)
    out = polish_canvas(canvas, ArtDirection.default(), expanded)
    colors = out.colors()
    assert expanded.rgba("steel_shadow") in colors
    assert expanded.rgba("steel_bright") in colors


def test_hand_declared_ramp_group_shades_with_authored_steps() -> None:
    # Warden-style: hand-declared ramp steps sharing a `ramp` id. Shading a
    # mid-tone pixel must use the *authored* dark/light steps of that group —
    # not derived tones generated from the mid colour.
    palette = Palette(
        id="p",
        colors=[
            PaletteColor(id="armor_dark", hex="#2b3648", ramp="armor", role="shadow"),
            PaletteColor(id="armor_mid", hex="#4a5c7d", ramp="armor"),
            PaletteColor(id="armor_lite", hex="#7b93b8", ramp="armor", role="light"),
        ],
        auto_ramp=True,
        derive_outline=True,
    )
    expanded = resolve_palette(expand_palette(palette))
    mid = expanded.rgba("armor_mid")
    canvas = Canvas(36, 36)
    for y in range(2, 34):
        for x in range(2, 34):
            canvas.set_pixel(x, y, mid)
    out = polish_canvas(canvas, ArtDirection.default(), expanded)
    colors = out.colors()
    assert expanded.rgba("armor_dark") in colors
    assert expanded.rgba("armor_lite") in colors
    assert expanded.rgba("armor_mid") in colors


def test_polish_never_emits_pure_black() -> None:
    # Engineer's palette declares `shadow: #000000` and the ground shadow
    # darkens onto it; the polish pass must never leave pure-black pixels —
    # quantization nudges them to the palette's hue-tinted outline charcoal.
    palette = Palette(
        id="p",
        colors=[
            PaletteColor(id="shadow", hex="#000000"),
            PaletteColor(id="suit", hex="#3a5a78"),
        ],
        auto_ramp=True,
        derive_outline=True,
    )
    expanded = resolve_palette(expand_palette(palette))
    canvas = Canvas(10, 12)
    for y in range(2, 7):
        for x in range(2, 8):
            canvas.set_pixel(x, y, (0, 0, 0, 255))  # authored black trim
    out = polish_canvas(canvas, ArtDirection.default(), expanded)
    opaque = out.array[out.array[..., 3] != 0]
    assert opaque.shape[0] > 0
    assert not np.any(np.all(opaque[:, :3] == (0, 0, 0), axis=1))
    # The nudged pixels are still approved palette colours.
    for rgba in out.colors():
        assert expanded.contains_rgba(rgba), f"polished pixel {rgba} not in palette"


# --- size-adaptive shading radius ---------------------------------------------------------------


def test_small_sprite_keeps_mid_tone_centre() -> None:
    # Regression for the round-3 critic's banding gap: a 20x16 sprite (the
    # crawler shell's size) with the old fixed radius 4 lost its interior to
    # the lit band + shadow band + ink outline. The size-adaptive clamp must
    # shrink the bands to ~2px, so every pixel >= 3px from the silhouette edge
    # keeps the author's mid tone (RED) — the classic 2-3 tone ramp with a
    # preserved centre, not a stack of flat bands.
    canvas = _rect_canvas(width=24, height=24, at=(2, 4), size=(20, 16))
    out = polish_canvas(canvas, ArtDirection.default(), _polish_palette())
    # Measure the interior on the RAW sprite mask (the ground-shadow band the
    # pass appends below the feet is not part of the sprite's silhouette).
    raw_opaque = canvas.array[..., 3] != 0
    up, down, left, right = _run_distances(raw_opaque)
    min_dist = np.minimum(np.minimum(up, down), np.minimum(left, right))
    interior = min_dist >= 3
    ys, xs = np.nonzero(interior)
    assert ys.size > 0, "expected an interior >= 3px from the silhouette edge"
    mid_count = sum(
        1
        for y, x in zip(ys.tolist(), xs.tolist(), strict=True)
        if out.get_pixel(x, y)[:3] == RED[:3]
    )
    assert mid_count == ys.size, f"{mid_count}/{ys.size} interior pixels kept the mid tone"


def test_large_sprite_keeps_full_light_radius() -> None:
    # A 64px sprite must keep the configured radius 4: the lit band reaches 4px
    # in from the lit edges (the pixel 4px from the top-left corner is lit, the
    # pixel 5px in is back on the mid tone) and the shadow band 4px in from the
    # dark edges. The size-adaptive clamp must NOT shrink bands on large art.
    out = polish_canvas(
        _rect_canvas(width=68, height=68, at=(2, 2), size=(64, 64)),
        ArtDirection.default(),
        _polish_palette(),
    )
    lit = out.get_pixel(5, 5)  # 4px from the top and left edges
    mid = out.get_pixel(6, 34)  # 5px from the top edge, far from every side
    dark = out.get_pixel(63, 63)  # 4px from the bottom and right edges
    assert sum(lit[:3]) > sum(mid[:3]) > sum(dark[:3])
    assert mid[:3] == RED[:3]  # the centre band stays exactly on the base colour


def test_mirrored_frame_keeps_world_fixed_light() -> None:
    # Regression for the mirrored-frame light fix: the composite is flipped
    # before polish and the light stays world-fixed, so EVERY direction's
    # shadow — the mirrored east frame included — lies on the frame's own
    # lower-right, exactly as if one arena light lit the whole sheet. If the
    # angle were mirrored for mirrored frames, east's shadow would sit on its
    # lower-left and this test would fail.
    doc = _mirror_doc()
    palette = resolve_palette(doc.palette)
    expanded = resolve_palette(palette_for_polish(doc.palette))
    red_shadow = expanded.rgba("red_shadow")
    backend = LocalRenderBackend()
    frames = resolve_frames(doc)
    assert {f.direction for f in frames} == {"west", "east"}
    # The sprite's own vertical centre: the body rect spans rows 4..35 on a 40px
    # canvas (y0=4, height=32). We use the SPEC geometry, not the polished bbox,
    # because the ground-shadow band below the sprite drags the bbox centre
    # downward — the shadow tone must be judged against the sprite, not the
    # shadow it casts.
    sprite_cy = 4 + 32 / 2  # 20.0
    for frame in frames:
        canvas = backend.render_frame(doc, frame, palette, art_direction=ArtDirection.default())
        x0, _y0, x1, _y1 = canvas.bbox()
        cx = (x0 + x1 - 1) / 2
        mask = np.all(canvas.array == np.array(red_shadow, dtype=np.uint8), axis=-1)
        assert mask.any(), f"no shadow tone in {frame.direction} frame"
        ys, xs = np.nonzero(mask)
        assert xs.mean() > cx, f"shadow not on the right side: {frame.direction}"
        assert ys.mean() > sprite_cy, f"shadow not on the bottom side: {frame.direction}"


# --- LocalRenderBackend wiring ------------------------------------------------------------------


def test_render_frame_raw_without_art_direction() -> None:
    doc = _sprite_doc()
    palette = resolve_palette(doc.palette)
    frame = resolve_frames(doc)[0]
    canvas = LocalRenderBackend().render_frame(doc, frame, palette)
    # No polish: the flat palette colour survives exactly (edge pixels included).
    assert canvas.get_pixel(1, 1) == (200, 50, 50, 255)  # #c83232
    assert canvas.colors() == {(200, 50, 50, 255)}


def test_render_frame_applies_polish_when_directed() -> None:
    doc = _sprite_doc()
    palette = resolve_palette(doc.palette)
    frame = resolve_frames(doc)[0]
    canvas = LocalRenderBackend().render_frame(
        doc, frame, palette, art_direction=ArtDirection.default()
    )
    assert len(canvas.colors()) >= 2
    # The silhouette boundary is inked (the doc declares an outline colour, so
    # the polish pass prefers it as the exact quantization target).
    assert canvas.get_pixel(1, 1) == OUTLINE
    # Alpha stays binary.
    alpha = canvas.array[..., 3]
    assert np.all((alpha == 0) | (alpha == 255))
    # Every pixel the polished render wrote is an approved colour of the
    # render-time expanded palette — the PIX003/PIX004 integration contract.
    expanded = resolve_palette(
        expand_palette(doc.palette.model_copy(update={"auto_ramp": True, "derive_outline": True}))
    )
    for rgba in canvas.colors():
        assert expanded.contains_rgba(rgba), f"polished pixel {rgba} not in expanded palette"


def test_render_frame_raw_and_polished_share_shape_colour_ids() -> None:
    # Compositing keeps the flat declared palette (shape colour ids resolve the
    # same); the polish palette only adds quantization targets. The declared
    # mid tone must survive the polished render byte-identically somewhere.
    doc = _sprite_doc()
    palette = resolve_palette(doc.palette)
    frame = resolve_frames(doc)[0]
    canvas = LocalRenderBackend().render_frame(
        doc, frame, palette, art_direction=ArtDirection.default()
    )
    assert (200, 50, 50, 255) in canvas.colors()  # #c83232, the declared red


# --- per-region form shading (coherence item) ------------------------------------------------


def _two_region_doc() -> Any:
    """A 64x64 sprite with a big body rect and a smaller 'cap' rect drawn on top.

    The cap OVERLAPS the body (its bottom row sits on top of the body), so the
    cap's underside is interior to the sprite silhouette — global edge-distance
    banding leaves it flat mid, exactly the gap the round-4 critic flagged as the
    residual: forms whose edges don't touch the outer silhouette get no form
    shading. With the compositor's region tags, the cap is shaded against its OWN
    silhouette and its underside gains a shadow band.
    """
    return parse_asset_doc(
        {
            "schema_version": 1,
            "asset": {"id": "two", "type": "character", "canvas": [64, 64]},
            "palette": {
                "id": "p",
                "colors": [
                    {"id": "red", "hex": "#c83232"},
                    {"id": "blue", "hex": "#3264c8"},
                    {"id": "outline", "hex": "#1a1a1f", "role": "outline"},
                ],
            },
            "directions": ["south"],
            "anchors": {"root": [0, 0]},
            "regions": {
                "body": {
                    "anchor": "root",
                    "layer": 0,
                    "shapes": [{"op": "rect", "color": "red", "at": [4, 16], "size": [56, 44]}],
                },
                "cap": {
                    "anchor": "root",
                    "layer": 10,
                    "shapes": [{"op": "rect", "color": "blue", "at": [12, 4], "size": [40, 16]}],
                },
            },
            "direction_overrides": {},
            "animations": {
                "idle": {
                    "loop": True,
                    "frames": [{"duration_ms": 100, "events": [], "transforms": {}}],
                }
            },
            "export": {},
            "validation": {},
        }
    )


def _two_region_tags() -> tuple[Canvas, NDArray[np.int64]]:
    """The flat composite + region-ownership tags for `_two_region_doc`."""
    doc = _two_region_doc()
    palette = resolve_palette(doc.palette)
    layers = plan_layers(doc, doc.regions, doc.anchors, {}, palette)
    return composite_tagged(doc.asset.canvas, layers, palette)


def test_composite_tagged_matches_composite() -> None:
    # The tagged path must be byte-identical to the plain composite: callers that
    # switch to it for per-region shading never change the composed pixels.
    doc = _two_region_doc()
    palette = resolve_palette(doc.palette)
    layers = plan_layers(doc, doc.regions, doc.anchors, {}, palette)
    plain = composite(doc.asset.canvas, layers, palette)
    tagged, tags = composite_tagged(doc.asset.canvas, layers, palette)
    assert np.array_equal(plain.array, tagged.array)
    # Tag semantics: topmost region wins, transparent stays -1. The cap (layer
    # 10) owns its own pixels; the body (layer 0) owns everything below.
    assert tags[5, 31] == 1  # cap pixel
    assert tags[25, 31] == 0  # body pixel
    assert tags[0, 0] == -1  # transparent corner
    assert np.all((tags >= 0) == (plain.array[..., 3] != 0))


def test_per_region_shading_shades_interior_region_underside() -> None:
    # THE coherence item: with region tags, the cap's underside (an edge interior
    # to the sprite silhouette — the body extends below it) gains a shadow band
    # from its own geometry; the global edge-distance banding leaves it flat.
    # Probe the cap's bottom row centre (x=31, y=19): under global banding it is
    # mid blue (far from every silhouette edge); under per-region banding it is
    # the blue shadow ramp tone.
    canvas, tags = _two_region_tags()
    expanded = _polish_palette_for(_two_region_doc())
    blue_shadow = _rgb3(expanded.rgba("blue_shadow"))
    global_out = polish_canvas(canvas, ArtDirection.default(), expanded)
    per_region_out = polish_canvas(canvas, ArtDirection.default(), expanded, region_tags=tags)
    global_px = global_out.get_pixel(31, 19)[:3]
    per_region_px = per_region_out.get_pixel(31, 19)[:3]
    assert global_px == _rgb3(expanded.rgba("blue")), f"global banding left it {global_px}"
    assert per_region_px == blue_shadow, f"per-region shading gave {per_region_px}"
    # The body's own silhouette edges are unchanged (both passes shade the outer
    # boundary the same way): the body's bottom-left corner is shadow in both.
    assert global_out.get_pixel(7, 57)[:3] == per_region_out.get_pixel(7, 57)[:3]


def test_per_region_shading_is_deterministic() -> None:
    canvas, tags = _two_region_tags()
    expanded = _polish_palette_for(_two_region_doc())
    a = polish_canvas(canvas, ArtDirection.default(), expanded, region_tags=tags)
    b = polish_canvas(canvas, ArtDirection.default(), expanded, region_tags=tags)
    assert np.array_equal(a.array, b.array)


def test_flat_ground_shadow_region_keeps_global_treatment() -> None:
    # A thin authored ground-shadow ellipse (own interior < 3px) must NOT be
    # shaded as a volume — per-region banding would ring it with lit/shadow
    # bands. Its pixels keep the global edge-distance treatment, byte-identical
    # to polishing without tags.
    doc = parse_asset_doc(
        {
            "schema_version": 1,
            "asset": {"id": "sh", "type": "character", "canvas": [48, 48]},
            "palette": {
                "id": "p",
                "colors": [
                    {"id": "body", "hex": "#5b3a29"},
                    {"id": "shadow", "hex": "#000000"},
                    {"id": "outline", "hex": "#1a1a1f", "role": "outline"},
                ],
            },
            "directions": ["south"],
            "anchors": {"root": [0, 0]},
            "regions": {
                "shadow": {
                    "anchor": "root",
                    "layer": 0,
                    "shapes": [
                        {
                            "op": "ellipse",
                            "color": "shadow",
                            "at": [10, 38],
                            "size": [28, 3],
                        }
                    ],
                },
                "body": {
                    "anchor": "root",
                    "layer": 10,
                    "shapes": [{"op": "rect", "color": "body", "at": [14, 4], "size": [20, 32]}],
                },
            },
            "direction_overrides": {},
            "animations": {
                "idle": {
                    "loop": True,
                    "frames": [{"duration_ms": 100, "events": [], "transforms": {}}],
                }
            },
            "export": {},
            "validation": {},
        }
    )
    palette = resolve_palette(doc.palette)
    layers = plan_layers(doc, doc.regions, doc.anchors, {}, palette)
    canvas, tags = composite_tagged(doc.asset.canvas, layers, palette)
    expanded = _polish_palette_for(doc)
    global_out = polish_canvas(canvas, ArtDirection.default(), expanded)
    per_region_out = polish_canvas(canvas, ArtDirection.default(), expanded, region_tags=tags)
    shadow_mask = canvas.array[..., 3] != 0
    shadow_mask &= (canvas.array[..., 0] == 0) & (canvas.array[..., 1] == 0)
    shadow_mask &= canvas.array[..., 2] == 0
    assert shadow_mask.any()
    assert np.array_equal(
        global_out.array[shadow_mask], per_region_out.array[shadow_mask]
    ), "flat shadow region changed under per-region shading"


def test_render_frame_uses_per_region_shading_end_to_end() -> None:
    # The LocalRenderBackend wiring: render_frame with art_direction threads the
    # compositor tags into the polish pass, so the interior cap underside is
    # shadowed; without art_direction the raw composite is untouched.
    doc = _two_region_doc()
    palette = resolve_palette(doc.palette)
    frame = resolve_frames(doc)[0]
    raw = LocalRenderBackend().render_frame(doc, frame, palette)
    assert raw.get_pixel(31, 19) == (50, 100, 200, 255)  # #3264c8 blue, unpolished
    polished = LocalRenderBackend().render_frame(
        doc, frame, palette, art_direction=ArtDirection.default()
    )
    expanded = _polish_palette_for(doc)
    assert polished.get_pixel(31, 19)[:3] == _rgb3(expanded.rgba("blue_shadow"))
