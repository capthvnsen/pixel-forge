from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from pixel_forge.domain.palette import cielab_lightness, palette_for_polish, resolve_palette
from pixel_forge.errors import ForgeError
from pixel_forge.rendering.canvas import RGBA, Canvas
from pixel_forge.rendering.local import LocalRenderBackend, expand_terrain_variants
from pixel_forge.rendering.sheet import (
    build_seam_map,
    build_variant_layout,
    build_variation_tiles,
)
from pixel_forge.rendering.terrain import (
    layout_autocorrelation,
    layout_max_autocorrelation,
)
from pixel_forge.schemas import ArtDirection, Palette, TerrainAsset, parse_asset_doc

BLUE: RGBA = (40, 80, 220, 255)
YELLOW: RGBA = (240, 220, 60, 255)
GRASS_FILL: RGBA = (76, 154, 42, 255)
GRASS_DETAIL: RGBA = (63, 127, 34, 255)
EDGE: RGBA = (46, 36, 31, 255)


def _palette(*colors: tuple[str, str]) -> Palette:
    return Palette(
        id="p", colors=[{"id": color_id, "hex": hex_str} for color_id, hex_str in colors]
    )


def _grass_tile() -> Canvas:
    """16x16 tile with a grout ring, flat fill, and two authored detail pixels."""
    c = Canvas(16, 16)
    c.draw_rect((0, 0), (16, 16), EDGE, fill=True)
    c.draw_rect((1, 1), (14, 14), GRASS_FILL, fill=True)
    c.set_pixel(4, 4, GRASS_DETAIL)
    c.set_pixel(9, 7, GRASS_DETAIL)
    return c


def test_build_variation_tiles_is_deterministic() -> None:
    base = _grass_tile()
    palette = resolve_palette(_palette(("red", "#dc2828"), ("blue", "#2850dc")))
    first = build_variation_tiles(base, palette, 4, seed=11)
    second = build_variation_tiles(base, palette, 4, seed=11)
    assert len(first) == len(second) == 4
    for a, b in zip(first, second, strict=True):
        assert np.array_equal(a.array, b.array)


def test_build_variation_tiles_produces_n_distinct_tiles() -> None:
    base = _grass_tile()
    palette = resolve_palette(_palette(("red", "#dc2828"), ("blue", "#2850dc")))
    variants = build_variation_tiles(base, palette, 4, seed=5)
    assert len(variants) == 4
    tiles = [base, *variants]
    for i in range(len(tiles)):
        for j in range(i + 1, len(tiles)):
            assert not np.array_equal(tiles[i].array, tiles[j].array), f"tiles {i} and {j}"

    # Different seeds produce different scatter patterns (same variant count).
    other = build_variation_tiles(base, palette, 4, seed=6)
    assert any(
        not np.array_equal(variant.array, other[k].array) for k, variant in enumerate(variants)
    )


def test_build_variation_tiles_preserves_seams() -> None:
    base = _grass_tile()
    palette = resolve_palette(_palette(("red", "#dc2828"), ("blue", "#2850dc")))
    for variant in build_variation_tiles(base, palette, 4, seed=5):
        assert np.array_equal(variant.array[0, :, :], base.array[0, :, :])
        assert np.array_equal(variant.array[-1, :, :], base.array[-1, :, :])
        assert np.array_equal(variant.array[:, 0, :], base.array[:, 0, :])
        assert np.array_equal(variant.array[:, -1, :], base.array[:, -1, :])


def test_build_variation_tiles_scatters_requested_detail_colors() -> None:
    base = _grass_tile()
    palette = resolve_palette(_palette(("red", "#dc2828"), ("blue", "#2850dc")))
    variants = build_variation_tiles(base, palette, 2, seed=3, detail_colors=[BLUE, YELLOW])
    # Variant i paints colors[(i + j) % 2] at its j-th scatter pixel.
    assert BLUE in variants[0].colors()
    assert YELLOW in variants[1].colors()
    # Every scatter pixel lands inside the 1px border ring.
    for variant in variants:
        assert np.array_equal(variant.array[0, :, :], base.array[0, :, :])
        assert np.array_equal(variant.array[-1, :, :], base.array[-1, :, :])
        assert np.array_equal(variant.array[:, 0, :], base.array[:, 0, :])
        assert np.array_equal(variant.array[:, -1, :], base.array[:, -1, :])


def test_build_variation_tiles_derives_detail_colors_from_palette_roles() -> None:
    palette = Palette(
        id="p",
        colors=[
            {"id": "fill", "hex": "#4c9a2a"},
            {"id": "fill_light", "hex": "#7fc95a", "role": "light"},
            {"id": "fill_shadow", "hex": "#2f6618", "role": "shadow"},
        ],
    )
    base = Canvas(16, 16)
    base.draw_rect((0, 0), (16, 16), EDGE, fill=True)
    base.draw_rect((1, 1), (14, 14), GRASS_FILL, fill=True)
    variants = build_variation_tiles(base, resolve_palette(palette), 3, seed=1)
    role_colors = {(127, 201, 90, 255), (47, 102, 24, 255)}
    assert all(role_colors & variant.colors() for variant in variants)


def test_build_variation_tiles_rejects_impossible_requests() -> None:
    palette = resolve_palette(_palette(("red", "#dc2828"), ("blue", "#2850dc")))
    base = _grass_tile()
    with pytest.raises(ForgeError):
        build_variation_tiles(base, palette, 0)
    with pytest.raises(ForgeError):
        build_variation_tiles(base, palette, 4, pixels_per_variant=0)
    with pytest.raises(ForgeError):
        build_variation_tiles(base, palette, 4, clusters=0)
    with pytest.raises(ForgeError):
        build_variation_tiles(base, palette, 4, cluster_size=0)

    # 4x4 tile: interior is 2x2 = 4 pixels, 8 variants x 6 cluster pixels do not fit.
    small = Canvas(4, 4)
    small.draw_rect((0, 0), (4, 4), GRASS_FILL, fill=True)
    with pytest.raises(ForgeError):
        build_variation_tiles(small, palette, 8)

    # 2x2 tile has no interior at all.
    with pytest.raises(ForgeError):
        build_variation_tiles(Canvas(2, 2), palette, 2)


# --- cluster mode: organic tufts, not isolated speckles -------------------------------


def _diff_positions(base: Canvas, variant: Canvas) -> list[tuple[int, int]]:
    """(x, y) positions where `variant` differs from `base`, sorted."""
    ys, xs = np.nonzero(np.any(variant.array != base.array, axis=-1))
    return sorted((int(x), int(y)) for x, y in zip(xs, ys, strict=True))


def test_build_variation_tiles_cluster_mode_is_deterministic_and_seeded() -> None:
    base = _grass_tile()
    palette = resolve_palette(_palette(("red", "#dc2828"), ("blue", "#2850dc")))
    first = build_variation_tiles(base, palette, 4, seed=11)
    second = build_variation_tiles(base, palette, 4, seed=11)
    assert len(first) == len(second) == 4
    for a, b in zip(first, second, strict=True):
        assert np.array_equal(a.array, b.array)
    other = build_variation_tiles(base, palette, 4, seed=12)
    assert any(not np.array_equal(variant.array, other[k].array) for k, variant in enumerate(first))


def test_build_variation_tiles_tufts_are_organic_blobs() -> None:
    base = _grass_tile()
    palette = resolve_palette(_palette(("red", "#dc2828"), ("blue", "#2850dc")))
    variants = build_variation_tiles(base, palette, 4, seed=11, shade=False)
    for i, variant in enumerate(variants):
        diff = _diff_positions(base, variant)
        # Re-scatter displaces the base's 2 authored speckles (erased and
        # re-placed at new seeded positions) plus paints tufts: clearly more
        # than the round-2 scatter of 3 isolated px.
        assert len(diff) >= 6, (i, diff)
        # The tufts are organic: at least two 4-neighbour-connected blobs of
        # >= 2 pixels (the re-scattered isolated speckles need no neighbours).
        remaining = set(diff)
        blobs = 0
        while remaining:
            stack = [remaining.pop()]
            size = 0
            while stack:
                x, y = stack.pop()
                size += 1
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    neighbour = (x + dx, y + dy)
                    if neighbour in remaining:
                        remaining.discard(neighbour)
                        stack.append(neighbour)
            if size >= 2:
                blobs += 1
        assert blobs >= 2, (i, diff)
        # Interior-only: never touches the 1px border ring.
        assert all(1 <= x <= 14 and 1 <= y <= 14 for x, y in diff)


def _detailed_grass_tile() -> Canvas:
    """16x16 tile with a grout ring, flat fill, and 6 authored detail pixels —
    the demo grass base's authored skeleton."""
    c = Canvas(16, 16)
    c.draw_rect((0, 0), (16, 16), EDGE, fill=True)
    c.draw_rect((1, 1), (14, 14), GRASS_FILL, fill=True)
    for x, y in ((3, 3), (11, 4), (5, 9), (10, 8), (7, 12), (13, 6)):
        c.set_pixel(x, y, GRASS_DETAIL)
    return c


def test_build_variation_tiles_rescatters_the_full_interior() -> None:
    """Round-4 lattice fix: variants must DISPLACE the base's authored detail,
    not only add tufts on top of it — otherwise every cell in a field shares
    the base's speckle skeleton at identical offsets and the field reads as a
    copy-pasted wallpaper."""
    base = _detailed_grass_tile()
    palette = resolve_palette(_palette(("red", "#dc2828"), ("blue", "#2850dc")))
    authored = [(3, 3), (11, 4), (5, 9), (10, 8), (7, 12), (13, 6)]
    variants = build_variation_tiles(base, palette, 4, seed=11)
    for i, variant in enumerate(variants):
        diff = _diff_positions(base, variant)
        # Re-scatter means a clearly-visible displacement: erasing the 6
        # authored speckles plus painting tufts and re-scattered speckles lands
        # well above the round-3 4-8px (imperceptible) delta.
        assert len(diff) >= 12, (i, diff)
        # The authored skeleton is displaced: at least half the base's speckle
        # positions are erased back to the flat fill in each variant.
        erased = sum(1 for x, y in authored if variant.get_pixel(x, y) == GRASS_FILL)
        assert erased >= 3, (i, authored, erased)
        # Interior-only: never touches the 1px border ring.
        assert all(1 <= x <= 14 and 1 <= y <= 14 for x, y in diff)
    for variant in variants:
        assert np.array_equal(variant.array[0, :, :], base.array[0, :, :])
        assert np.array_equal(variant.array[-1, :, :], base.array[-1, :, :])
        assert np.array_equal(variant.array[:, 0, :], base.array[:, 0, :])
        assert np.array_equal(variant.array[:, -1, :], base.array[:, -1, :])


def test_build_variation_tiles_cluster_mode_preserves_seams() -> None:
    base = _grass_tile()
    palette = resolve_palette(_palette(("red", "#dc2828"), ("blue", "#2850dc")))
    for variant in build_variation_tiles(base, palette, 4, seed=11):
        assert np.array_equal(variant.array[0, :, :], base.array[0, :, :])
        assert np.array_equal(variant.array[-1, :, :], base.array[-1, :, :])
        assert np.array_equal(variant.array[:, 0, :], base.array[:, 0, :])
        assert np.array_equal(variant.array[:, -1, :], base.array[:, -1, :])


def test_build_variation_tiles_cluster_mode_uses_detail_colors() -> None:
    base = _grass_tile()
    palette = resolve_palette(_palette(("red", "#dc2828"), ("blue", "#2850dc")))
    variants = build_variation_tiles(
        base, palette, 4, seed=3, detail_colors=[BLUE, YELLOW], clusters=2, cluster_size=3
    )
    for variant in variants:
        assert BLUE in variant.colors() and YELLOW in variant.colors()


# --- regression: a tiled field must show no visible 16px lattice ----------------------
#
# The round-2 ring was a uniform 1px band of the material's *darkest ramp tone*
# (#265b19 on grass), which stayed visible as a dark green lattice over every
# 16px tile boundary. `tint_tile_ring` now tone-matches the ring to the dominant
# interior colour exactly, so a 4x4 field of one grass tile + 3 variants must
# show ring pixels within 2 CIE-L* steps of the interior (delta 0 here), and the
# ring must be a light material tone, never a dark band.


def _grass_terrain_doc() -> Any:
    """16x16 grass terrain: grout ring + fill + 6 detail pixels, 4 variations."""
    data = {
        "schema_version": 1,
        "asset": {"id": "field", "type": "terrain", "canvas": [16, 16]},
        "palette": {
            "id": "p",
            "auto_ramp": True,
            "colors": [
                {"id": "tile_edge", "hex": "#2e241f", "ramp_steps": 1},
                {"id": "grass_base", "hex": "#4c9a2a", "ramp_steps": 3},
                {"id": "grass_detail", "hex": "#3f7f22", "ramp_steps": 1},
            ],
        },
        "export": {},
        "validation": {},
        "tiles": {
            "grass": {
                "size": [16, 16],
                "terrain": "grass",
                "variations": 4,
                "anchors": {"origin": [0, 0]},
                "regions": {
                    "edge": {
                        "anchor": "origin",
                        "layer": 0,
                        "shapes": [
                            {"op": "rect", "color": "tile_edge", "at": [0, 0], "size": [16, 16]}
                        ],
                    },
                    "fill": {
                        "anchor": "origin",
                        "layer": 1,
                        "shapes": [
                            {"op": "rect", "color": "grass_base", "at": [1, 1], "size": [14, 14]}
                        ],
                    },
                    "detail": {
                        "anchor": "origin",
                        "layer": 2,
                        "shapes": [
                            {"op": "pixel", "color": "grass_detail", "at": [3, 3]},
                            {"op": "pixel", "color": "grass_detail", "at": [11, 4]},
                            {"op": "pixel", "color": "grass_detail", "at": [5, 9]},
                            {"op": "pixel", "color": "grass_detail", "at": [10, 8]},
                            {"op": "pixel", "color": "grass_detail", "at": [7, 12]},
                            {"op": "pixel", "color": "grass_detail", "at": [13, 6]},
                        ],
                    },
                },
            }
        },
        "terrain_sets": {},
        "transitions": [],
    }
    doc = parse_asset_doc(data)
    assert isinstance(doc, TerrainAsset)
    return doc


def test_field_shows_no_visible_tile_lattice() -> None:
    doc = _grass_terrain_doc()
    backend = LocalRenderBackend()
    palette = resolve_palette(palette_for_polish(doc.palette))
    tiles = {
        tile_id: backend.render_tile(
            doc, tile_id, palette, art_direction=ArtDirection.terrain_default()
        )
        for tile_id in sorted(doc.tiles)
    }
    cells = expand_terrain_variants(doc, tiles, palette)
    assert set(cells) == {"grass", "grass.v1", "grass.v2", "grass.v3"}

    # Dominant interior colour (the fill): every ring pixel must equal it, so a
    # ring pixel and its interior neighbour differ by 0 CIE-L* steps.
    base = cells["grass"]
    ring_colors = set()
    for x in range(base.width):
        ring_colors.add(tuple(base.get_pixel(x, 0)))
        ring_colors.add(tuple(base.get_pixel(x, base.height - 1)))
    for y in range(base.height):
        ring_colors.add(tuple(base.get_pixel(0, y)))
        ring_colors.add(tuple(base.get_pixel(base.width - 1, y)))
    assert ring_colors == {GRASS_FILL}
    assert cielab_lightness(GRASS_FILL) >= 40.0  # a light material tone, not a dark band

    # 4x4 field mixing the base tile + all 3 variants: every boundary pair
    # (ring pixel vs the interior pixel it borders) must stay within 2 L* steps.
    ids = ["grass", "grass.v1", "grass.v2", "grass.v3"]
    layout = [[ids[(x * 2 + y) % 4] for x in range(4)] for y in range(4)]
    field = build_seam_map(cells, layout)
    interior_l = cielab_lightness(GRASS_FILL)
    arr = field.array
    h, w = arr.shape[:2]
    # Every 16px boundary line: the pixels on both sides of the seam.
    for y in range(0, h, 16):
        for x in range(w):
            if y == 0 or y == h - 1:
                continue
            left = arr[y - 1, x, :3]
            right = arr[y, x, :3]
            assert abs(cielab_lightness((*left, 255)) - interior_l) <= 2.0, (y, x, left)
            assert abs(cielab_lightness((*right, 255)) - interior_l) <= 2.0, (y, x, right)
    for x in range(0, w, 16):
        for y in range(h):
            if x == 0 or x == w - 1:
                continue
            above = arr[y, x - 1, :3]
            below = arr[y, x, :3]
            assert abs(cielab_lightness((*above, 255)) - interior_l) <= 2.0, (y, x, above)
            assert abs(cielab_lightness((*below, 255)) - interior_l) <= 2.0, (y, x, below)
    # The 16px grid is literally invisible: no dark lattice colour appears at all.
    assert (46, 36, 31) not in {tuple(px) for px in arr.reshape(-1, 4)[:, :3]}


# --- round-4 regressions: content repetition is the lattice now -----------------------
#
# The tone-matched ring made the BOUNDARY invisible (round-3), but the field
# still read as a 16px grid because every cell shared the base's authored
# speckle skeleton and the demo layout repeated variants with an exact period.
# These tests pin the round-4 fixes: full-interior re-scatter per variant, a
# seeded non-periodic per-cell layout, and material ramps reaching the field.


def test_build_variant_layout_is_seeded_deterministic_and_non_periodic() -> None:
    rows = [["grass"] * 8 for _ in range(8)]
    counts = {"grass": 4}
    a = build_variant_layout(rows, counts, seed=0)
    b = build_variant_layout(rows, counts, seed=0)
    assert a == b
    c = build_variant_layout(rows, counts, seed=1)
    assert a != c
    # No two orthogonally-adjacent cells ever carry the same pattern.
    for y in range(8):
        for x in range(8):
            if x + 1 < 8:
                assert a[y][x] != a[y][x + 1], (x, y)
            if y + 1 < 8:
                assert a[y][x] != a[y + 1][x], (x, y)
    # The hash doesn't degenerate: every pattern appears, roughly balanced.
    assert len({tid for row in a for tid in row}) == 4
    counts_by_pattern = {tid: sum(row.count(tid) for row in a) for tid in set(row[0] for row in a)}
    assert max(counts_by_pattern.values()) <= 20


def test_build_variant_layout_no_2nd_cell_variant_checkerboard() -> None:
    """Round-6 regression: no two cells two apart along rows/columns may carry
    the same variant, deterministically over several seeds.

    The round-5 bump only forbade orthogonally-adjacent repeats, so the same
    variant survived every 2nd cell (measured row
    `[v1, base, v1, v2, v1, v2, v3, base]` -> grass.v1 at cols 0,2,4 = a 32px
    checker of pixel-identical detail), which the 16/48px autocorrelation bars
    do not catch (offset-2 AC 0.222-0.333). The bump now also differs from
    cells 2 away, and dead ends are repaired deterministically, so the layout
    is a pure function of (rows, variations, seed) with zero distance-2
    duplicates at offsets (2, 0) and (0, 2) — plus the round-5 (1, 0)/(0, 1)
    rule.
    """
    rows = [["grass"] * 8 for _ in range(8)]
    counts = {"grass": 4}
    for seed in range(9):
        layout = build_variant_layout(rows, counts, seed=seed)
        # Deterministic: same seed -> identical layout, every time.
        assert build_variant_layout(rows, counts, seed=seed) == layout
        for y in range(8):
            for x in range(8):
                if x + 2 < 8:
                    assert layout[y][x] != layout[y][x + 2], (x, y, seed)
                if y + 2 < 8:
                    assert layout[y][x] != layout[y + 2][x], (x, y, seed)
                if x + 1 < 8:
                    assert layout[y][x] != layout[y][x + 1], (x, y, seed)
                if y + 1 < 8:
                    assert layout[y][x] != layout[y + 1][x], (x, y, seed)


def test_field_content_autocorrelation_16px_is_below_0_3() -> None:
    """Round-4 acceptance: an assembled field of one base tile plus variants
    must not repeat its content at a 16px period. The round-3 field scored
    0.974 at 16px (the `(x*7 + y*3) % 3` layout repeated the same variant down
    every column); the seeded per-cell layout with no orthogonally-adjacent
    repeats scores 0.0.
    """
    doc = _grass_terrain_doc()
    backend = LocalRenderBackend()
    palette = resolve_palette(palette_for_polish(doc.palette))
    tiles = {
        tile_id: backend.render_tile(
            doc, tile_id, palette, art_direction=ArtDirection.terrain_default()
        )
        for tile_id in sorted(doc.tiles)
    }
    cells = expand_terrain_variants(doc, tiles, palette)
    rows = [["grass"] * 16 for _ in range(16)]
    layout = build_variant_layout(rows, {"grass": 4}, seed=0)
    for row in layout:
        for tile_id in row:
            assert tile_id in cells
    field = build_seam_map(cells, layout)
    # 16px (one-cell) offsets: the round-4 acceptance bar is < 0.3.
    assert layout_autocorrelation(layout, offset=(1, 0)) < 0.3
    assert layout_autocorrelation(layout, offset=(0, 1)) < 0.3
    # No exact periodicity at any offset (the round-3 field was 1.000 at 48px).
    assert layout_max_autocorrelation(layout, min_pairs=16) < 0.75
    assert (field.width, field.height) == (16 * 16, 16 * 16)


def test_terrain_interior_shows_light_and_shadow_ramp_tones() -> None:
    """Round-4 secondary fix: the material ramps must reach the FIELD, not sit
    on a 1px ring. Every atlas cell's interior must carry > 5% non-fill pixels
    and both the material's `_light` and `_shadow` ramp tones."""
    doc = _grass_terrain_doc()
    backend = LocalRenderBackend()
    palette = resolve_palette(palette_for_polish(doc.palette))
    tiles = {
        tile_id: backend.render_tile(
            doc, tile_id, palette, art_direction=ArtDirection.terrain_default()
        )
        for tile_id in sorted(doc.tiles)
    }
    cells = expand_terrain_variants(doc, tiles, palette)
    light = palette.rgba("grass_base_light")
    shadow = palette.rgba("grass_base_shadow")
    for name, cell in cells.items():
        interior = cell.array[1:-1, 1:-1]
        opaque = interior[..., 3] != 0
        total = int(opaque.sum())
        non_fill = int(
            np.count_nonzero(
                opaque & np.any(interior[..., :3] != np.array(GRASS_FILL[:3]), axis=-1)
            )
        )
        assert non_fill / total > 0.05, (name, non_fill, total)
        rgb = set(map(tuple, interior[opaque][:, :3].reshape(-1, 3)))
        assert light[:3] in rgb and shadow[:3] in rgb, name
