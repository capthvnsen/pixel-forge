"""Terrain-quality helpers: colour-family analysis and transition-blend checking.

Pure functions of rendered tiles plus a palette — no rendering, no randomness —
so they are safe to call from the validation rules and from the critic's "edge
treatment" judgment alike.

Colour families
---------------
A pixel's *family* is the palette colour id whose hex matches it, collapsed onto
the colour's `ramp` group. The render-polish pass quantizes every pixel it
writes onto the palette-for-polish expanded palette, shading each material's
pixels with that material's ramp tones (e.g. `grass_base_shadow` /
`grass_base_light` share the `grass_base` ramp group). Grouping by ramp group is
what lets the imbalance rule and the blend checker see "grass" and "dirt" as
single materials instead of a dozen ramp tones. Pixels whose colour is not in
the palette form their own family keyed by hex.

Transition blending
-------------------
A transition tile's interior contains two materials: the tile's own terrain
(the dominant family, e.g. dirt) and the encroaching `from_terrain` patch (the
second-most-common family, e.g. grass). Where they meet there is a boundary;
professional top-down art (e.g. the CraftPix reference) breaks that boundary
into a jagged, stepped 2px+ blend zone with weeds poking across, rather than
a single hard line. `check_transition_blends` measures two things about the
boundary — for an N/S transition, how rows behave across the interior
columns; for E/W, how columns behave across the interior rows:

- **blend width** — how far the patch's boundary *wanders*: the max extent
  minus the min extent of the patch's edge across the boundary lines (0 =
  perfectly straight = a hard 1px line).
- **blend coverage** — what *fraction* of the boundary lines interleave at
  least 1px beyond the straight-line baseline: whether the wander is a dense
  organic tuft across most of the boundary (~1.0) or a couple of sparse weeds
  poking through one column (~0.07).

A transition is `is_hard` when the blend width is narrower than
`min_blend_width` OR the coverage is below `min_blend_coverage` — so sparse
2-4px weeds can no longer score the same as a genuinely blended edge. Corner
transitions are judged on both axes (`min` of the row and column statistics):
a clean square corner patch has width 0 and coverage 0 on both axes, a
properly stepped corner wanders 2+ rows and 2+ columns across most of its
extent.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from pixel_forge.domain.palette import ResolvedPalette, hex_to_rgba
from pixel_forge.rendering.canvas import Canvas
from pixel_forge.schemas.asset import TerrainAsset

# The documented professional target: a transition boundary should read as a
# ~2px stepped/interleaved blend zone, not a hard 1px line.
MIN_EDGE_BLEND_WIDTH = 2

# And the interleave must be *wide*, not just deep: at least this fraction of
# the boundary's columns/rows must step at least 1px past the straight-line
# baseline. A single 2px-deep weed poking across a 14-column boundary is a
# 0.07 coverage — sparse noise, not a blend zone — while dense organic tufts
# interleave across most of the boundary.
MIN_BLEND_COVERAGE = 0.25


@dataclass(frozen=True)
class FamilyGrid:
    """Family index per pixel: `-1` outside the interior or transparent.

    `names[i]` is the human-readable family name for index `i`. Positions are
    in canvas coordinates (y, x), matching `Canvas.array` indexing.
    """

    array: NDArray[np.int32]  # (h, w)
    names: tuple[str, ...]

    def family_positions(self, family: int) -> NDArray[np.int64]:
        """(n, 2) array of (y, x) positions for one family index, row-major."""
        ys, xs = np.nonzero(self.array == family)
        return np.column_stack((ys, xs)).astype(np.int64)

    def counts(self) -> dict[int, int]:
        """Family index -> interior pixel count (transparent/outside excluded)."""
        values, counts = np.unique(self.array, return_counts=True)
        return {
            int(family): int(count)
            for family, count in zip(values, counts, strict=True)
            if family >= 0
        }

    def dominant(self) -> int | None:
        """Most common family index; ties go to the higher index (deterministic)."""
        counts = self.counts()
        if not counts:
            return None
        return max(counts, key=lambda family: (counts[family], family))


def interior_family_grid(
    canvas: Canvas, palette: ResolvedPalette, *, border: int = 1
) -> FamilyGrid:
    """Family index per interior pixel of `canvas`.

    The `border`-pixel ring is excluded (index -1): terrain tiles carry a
    shared grout ring there whose colour is not a material. Transparent pixels
    are also -1. `border=0` analyses the whole canvas.
    """
    arr = canvas.array
    h, w = arr.shape[0], arr.shape[1]
    grid = np.full((h, w), -1, dtype=np.int32)
    if h <= 2 * border or w <= 2 * border:
        return FamilyGrid(array=grid, names=())

    family_by_rgba: dict[tuple[int, int, int, int], str] = {}
    names: list[str] = []
    index_by_name: dict[str, int] = {}
    for color in palette.palette.colors:
        name = color.ramp or color.id
        if name not in index_by_name:
            index_by_name[name] = len(names)
            names.append(name)
        family_by_rgba[hex_to_rgba(color.hex)] = name

    for y in range(border, h - border):
        for x in range(border, w - border):
            r, g, b, a = arr[y, x]
            if a != 255:
                continue
            matched = family_by_rgba.get((int(r), int(g), int(b), 255))
            if matched is None:
                matched = f"#{int(r):02x}{int(g):02x}{int(b):02x}"
                if matched not in index_by_name:
                    index_by_name[matched] = len(names)
                    names.append(matched)
            grid[y, x] = index_by_name[matched]
    return FamilyGrid(array=grid, names=tuple(names))


def interior_detail_positions(
    canvas: Canvas, palette: ResolvedPalette, *, border: int = 1
) -> list[tuple[int, int]]:
    """(x, y) positions of interior pixels whose family differs from the
    dominant interior family — the authored detail: blades, pebbles, shimmer.

    Shading ramp tones of the fill share its family and are excluded, so the
    polish pass does not turn every tile into "all detail on the light side".
    Returns positions in deterministic (x, y) order.
    """
    grid = interior_family_grid(canvas, palette, border=border)
    dominant = grid.dominant()
    if dominant is None:
        return []
    positions: list[tuple[int, int]] = []
    for family, _count in grid.counts().items():
        if family == dominant:
            continue
        for y, x in grid.family_positions(family):
            positions.append((int(x), int(y)))
    positions.sort()
    return positions


def _edge_spread(
    grid: FamilyGrid, *, axis: int, patch: int, border: int, high: bool
) -> tuple[int, int, float]:
    """Blend statistics for one transition boundary axis.

    axis=0: per interior column, the patch's extent rows; axis=1: per interior
    row, the patch's extent columns. `high=True` measures the patch's max row
    per column (a north patch's bottom edge) or max column per row (a west
    patch's right edge); `high=False` measures the min row per column (a south
    patch's top edge) or min column per row (an east patch's left edge) — the
    boundary always faces the other material.

    Returns `(blend_width, boundary_lines, blend_coverage)`:
    - `blend_width` is the spread (max extent - min extent over the
      columns/rows): 0 for a perfectly straight boundary — the hard 1px line.
    - `boundary_lines` is how many columns/rows the patch actually spans.
    - `blend_coverage` is the fraction of those boundary lines whose extent
      steps at least 1px beyond the straight-line baseline (min extent) — how
      *widely* the blend zone interleaves, not just how deep its deepest
      weed pokes. A dense organic tuft interleaving across most of the
      boundary scores ~1.0; one 2px weed in a 14-column boundary scores 0.07.
    """
    arr = grid.array
    h, w = arr.shape
    extents: list[int] = []
    if axis == 0:
        for x in range(border, w - border):
            rows = np.nonzero(arr[border : h - border, x] == patch)[0]
            if len(rows):
                extents.append(int(rows[-1] if high else rows[0]) + border)
    else:
        for y in range(border, h - border):
            cols = np.nonzero(arr[y, border : w - border] == patch)[0]
            if len(cols):
                extents.append(int(cols[-1] if high else cols[0]) + border)
    if not extents:
        return 0, 0, 0.0
    baseline = min(extents)
    blend_width = max(extents) - baseline
    interleaved = sum(1 for extent in extents if extent - baseline >= 1)
    return blend_width, len(extents), interleaved / len(extents)


# Which axis/side each transition mask's boundary wanders along. Edges use one
# axis; corners use both (the patch's bottom/top edge and its left/right edge).
_MASK_BOUNDARIES: dict[str, tuple[tuple[int, bool], ...]] = {
    "N": ((0, True),),
    "S": ((0, False),),
    "W": ((1, True),),
    "E": ((1, False),),
    "NE": ((0, True), (1, False)),
    "NW": ((0, True), (1, True)),
    "SE": ((0, False), (1, False)),
    "SW": ((0, False), (1, True)),
}


@dataclass(frozen=True)
class TransitionBlendReport:
    """Blend verdict for one transition tile."""

    from_terrain: str
    to_terrain: str
    tile_id: str
    mask: str
    is_hard: bool
    blend_width: int
    materials: int
    blend_coverage: float = 0.0
    boundary_columns: int = 0
    note: str = ""


def check_transition_blends(
    doc: TerrainAsset,
    tiles: Mapping[str, Canvas],
    palette: ResolvedPalette,
    *,
    min_blend_width: int = MIN_EDGE_BLEND_WIDTH,
    min_blend_coverage: float = MIN_BLEND_COVERAGE,
) -> list[TransitionBlendReport]:
    """Verdict per `doc.transitions` entry, sorted by (tile_id, mask).

    A transition whose tile is absent from `tiles` is skipped (unknown tile ids
    are TIL001's error to report). A tile whose interior is a single material is
    reported hard — there is no boundary to blend, the encroaching patch is
    missing. Everything else is judged on its boundary's blend statistics as
    described in the module docstring: a boundary is hard when its blend zone
    is narrower than `min_blend_width` px OR interleaves across fewer than
    `min_blend_coverage` of its boundary lines — a single 2px-deep weed poking
    across a 14-column boundary is still hard, because a 0.07-wide interleave
    is not a blend zone.
    """
    reports: list[TransitionBlendReport] = []
    for transition in sorted(doc.transitions, key=lambda t: (t.tile_id, t.mask)):
        canvas = tiles.get(transition.tile_id)
        if canvas is None:
            continue
        grid = interior_family_grid(canvas, palette)
        counts = grid.counts()
        materials = len(counts)
        if materials < 2:
            reports.append(
                TransitionBlendReport(
                    from_terrain=transition.from_terrain,
                    to_terrain=transition.to_terrain,
                    tile_id=transition.tile_id,
                    mask=transition.mask,
                    is_hard=True,
                    blend_width=0,
                    materials=materials,
                    note=(
                        "interior has a single material — the encroaching patch "
                        "may be missing"
                    ),
                )
            )
            continue
        dominant = grid.dominant()
        assert dominant is not None
        patch = max(
            (family for family in counts if family != dominant),
            key=lambda family: (counts[family], family),
        )
        stats = [
            _edge_spread(grid, axis=axis, patch=patch, border=1, high=high)
            for axis, high in _MASK_BOUNDARIES.get(transition.mask, ())
        ]
        # Unknown masks (schema forbids them, but be safe) have no boundary to
        # measure; only masks with at least one boundary axis are judged.
        if not stats:
            continue
        # Edges: one axis. Corners: both axes must step, so take the weaker one.
        blend_width = min(stat[0] for stat in stats)
        boundary_columns = min(stat[1] for stat in stats)
        blend_coverage = min(stat[2] for stat in stats)
        is_hard = blend_width < min_blend_width or blend_coverage < min_blend_coverage
        note = ""
        if is_hard:
            if blend_width < min_blend_width:
                note = (
                    f"boundary is a {blend_width}px blend zone "
                    f"(want >= {min_blend_width}px of stepped/interleaved edge)"
                )
            else:
                note = (
                    f"boundary blend only interleaves across "
                    f"{blend_coverage:.0%} of its {boundary_columns} "
                    f"columns/rows (want >= {min_blend_coverage:.0%}): "
                    "a few sparse weeds are not a blend zone"
                )
        reports.append(
            TransitionBlendReport(
                from_terrain=transition.from_terrain,
                to_terrain=transition.to_terrain,
                tile_id=transition.tile_id,
                mask=transition.mask,
                is_hard=is_hard,
                blend_width=blend_width,
                materials=materials,
                blend_coverage=blend_coverage,
                boundary_columns=boundary_columns,
                note=note,
            )
        )
    return reports


def layout_autocorrelation(
    layout: Sequence[Sequence[str]], *, offset: tuple[int, int] = (1, 0)
) -> float:
    """Fraction of cell pairs `offset` cells apart that carry the same tile id.

    The round-4 content-lattice probe: a periodic layout (e.g. the round-3
    demo's `(x*7 + y*3) % 3`, whose y-term vanished mod 3 and repeated the
    same variant down every column) scores ~1.0 at a one-cell offset; a seeded
    per-cell layout with 4 patterns scores ~0.25; `build_variant_layout`'s
    no-adjacent-repeat assignment scores 0.0 at offsets (1, 0) and (0, 1).
    Cells of different materials never match, so a straight transition row
    does not inflate the score of a material region. Deterministic.
    """
    height = len(layout)
    if height == 0:
        return 0.0
    width = len(layout[0])
    dx, dy = offset
    pairs = 0
    matches = 0
    for y in range(height):
        for x in range(width):
            tx, ty = x + dx, y + dy
            if 0 <= tx < width and 0 <= ty < height:
                pairs += 1
                if layout[y][x] == layout[ty][tx]:
                    matches += 1
    return matches / pairs if pairs else 0.0


def layout_max_autocorrelation(
    layout: Sequence[Sequence[str]], *, min_pairs: int = 8
) -> float:
    """Worst-case tile-id pair equality over every non-zero offset.

    1.0 at any offset means the field tiles exactly at that offset — an exact
    wallpaper (the round-3 field scored 1.000 at 48px). A seeded per-cell
    layout stays well below that at every offset: no exact periodicity, so no
    tile pattern repeats across the field at any offset. Offsets with fewer
    than `min_pairs` cell pairs are skipped — a corner offset with a single
    pair is measurement noise, not a period. Deterministic.
    """
    height = len(layout)
    width = len(layout[0]) if height else 0
    best = 0.0
    for dy in range(height):
        for dx in range(width):
            if dx == 0 and dy == 0:
                continue
            pairs = (width - dx) * (height - dy)
            if pairs < min_pairs:
                continue
            best = max(best, layout_autocorrelation(layout, offset=(dx, dy)))
    return best
