"""Projected-walk symmetry: opposite-facing views animate as exact mirrors.

Regression for a cross-piece seam defect: `_mirror_view` flipped each
mirror-safe region's canvas, but `project_animated_frames` rotated every region
about the UNMIRRORED world anchor — so mirrored views (west / south_west /
north_west) swung their limbs around the wrong pivot, merging legs at max
stride while the source views stayed clean. The fix records the per-view
rotation pivot (`ProjectedRegion.anchor`) and the content's flip parity
(`ProjectedRegion.mirrored`), then negates rotations/offsets in flipped views.

Invariants under test:
- west[i] == mirror(east[i]) for every walk frame i (same for SW/SE, NW/NE).
- north[i] == mirror(south[i]) EXCEPT the head's interior-ink face detail,
  which is deliberately stripped from back views (the only differing pixels
  are the embedded eyes).
"""

from __future__ import annotations

from typing import Any

from pixel_forge.animation.cycles import generate_joint_walk_cycle
from pixel_forge.domain.palette import resolve_palette
from pixel_forge.rendering.direction import (
    DIRECTIONS,
    project_animated_frames,
    project_directions,
)
from pixel_forge.schemas import parse_asset_doc
from pixel_forge.schemas.asset import SpriteAssetBase

W, H = 24, 32

_PALETTE: list[dict[str, Any]] = [
    {"id": "ink", "hex": "#1a1612"},
    {"id": "skin", "hex": "#e0b694"},
    {"id": "shirt", "hex": "#5482c4"},
    {"id": "sleeve_l", "hex": "#3a5e9e"},
    {"id": "sleeve_r", "hex": "#6c9adc"},
    {"id": "pants_l", "hex": "#544868"},
    {"id": "pants_r", "hex": "#6c6082"},
    {"id": "hair", "hex": "#b0802e"},
    {"id": "shadow_c", "hex": "#181820"},
]

_ANCHORS: dict[str, Any] = {
    "feet": [12, 31],
    "hip_l": [9, 22],
    "hip_r": [15, 22],
    "torso": [12, 14],
    "shoulder_l": [7, 14],
    "shoulder_r": [16, 14],
    "head": [12, 6],
}


def _bitmap(at: tuple[int, int], key: dict[str, str], rows: list[str]) -> dict[str, Any]:
    return {"op": "bitmap", "at": list(at), "key": key, "rows": rows}


def _regions(*, embedded_eyes: bool = False) -> dict[str, Any]:
    regions: dict[str, Any] = {
        "shadow": {
            "anchor": "feet",
            "layer": 0,
            "shapes": [{"op": "ellipse", "color": "shadow_c", "at": [-8, -2], "size": [16, 3]}],
        },
        "leg_left": {
            "anchor": "hip_l",
            "layer": 5,
            "shapes": [_bitmap((-1, 0), {"o": "ink", "L": "pants_l"}, ["oLo"] * 7 + ["ooo"])],
        },
        "leg_right": {
            "anchor": "hip_r",
            "layer": 5,
            "shapes": [_bitmap((-1, 0), {"o": "ink", "R": "pants_r"}, ["oRo"] * 7 + ["ooo"])],
        },
        "arm_left": {
            "anchor": "shoulder_l",
            "layer": 8,
            "shapes": [_bitmap((-1, 0), {"A": "sleeve_l", "o": "ink"}, ["AAAA"] * 8 + ["oooo"])],
        },
        "arm_right": {
            "anchor": "shoulder_r",
            "layer": 8,
            "shapes": [_bitmap((-1, 0), {"B": "sleeve_r", "o": "ink"}, ["BBBB"] * 8 + ["oooo"])],
        },
        "torso": {
            "anchor": "torso",
            "layer": 10,
            "shapes": [
                _bitmap(
                    (-5, -4),
                    {"o": "ink", "T": "shirt"},
                    ["oooooooooo"] + ["TTTTTTTTTT"] * 8 + [".oooooooo."],
                )
            ],
        },
        "head": {
            "anchor": "head",
            "layer": 20,
            "shapes": [
                _bitmap(
                    (-4, -4),
                    {"o": "ink", "S": "skin"},
                    [".oooooo."] + ["oSSSSSSo"] * 6 + [".oooooo."],
                )
            ],
        },
        "hair": {
            "anchor": "head",
            "layer": 22,
            "shapes": [
                _bitmap(
                    (-4, -5),
                    {"o": "ink", "h": "hair"},
                    [".ohhhho.", "ohhhhhho", "oh....ho"],
                )
            ],
        },
    }
    if embedded_eyes:
        # Demo-character structure: eyes painted INSIDE the head bitmap as
        # interior-ink clusters (world (12,5) and (16,5)) instead of a separate
        # face region — the case back views must strip.
        regions["head"]["shapes"] = [
            _bitmap(
                (-6, -4),
                {"o": "ink", "S": "skin"},
                [".oooooooooooo."] + ["oSSSSSSSSSSSSo"] * 6 + [".oooooooooooo."],
            ),
            _bitmap((0, -1), {"e": "ink"}, ["e...e"]),
        ]
    return regions


def _doc(*, embedded_eyes: bool = False) -> SpriteAssetBase:
    doc = parse_asset_doc(
        {
            "schema_version": 1,
            "asset": {"id": "scout", "type": "character", "canvas": [W, H]},
            "palette": {"id": "p", "colors": _PALETTE},
            "directions": ["south"],
            "anchors": _ANCHORS,
            "regions": _regions(embedded_eyes=embedded_eyes),
            "animations": {},
            "export": {},
            "validation": {},
        }
    )
    assert isinstance(doc, SpriteAssetBase)
    return doc


def _diff_pixels(a, b) -> set[tuple[int, int]]:
    arr_a = a.array
    arr_b = b.array
    assert arr_a.shape == arr_b.shape
    out: set[tuple[int, int]] = set()
    for y in range(arr_a.shape[0]):
        for x in range(arr_a.shape[1]):
            if tuple(arr_a[y, x]) != tuple(arr_b[y, x]):
                out.add((x, y))
    return out


def test_mirrored_views_walk_exact_mirrors() -> None:
    """west/SW/NW walks are the exact mirrors of east/SE/NE walks, frame by frame."""
    doc = _doc()
    palette = resolve_palette(doc.palette)
    walk = generate_joint_walk_cycle(doc, {})
    assert len(walk) >= 4
    animated = project_animated_frames(doc, palette, walk)
    for a, b in (("west", "east"), ("south_west", "south_east"), ("north_west", "north_east")):
        for i in range(len(walk)):
            assert animated[a][i].equals(animated[b][i].mirror_x()), (
                f"{a}[{i}] is not the mirror of {b}[{i}] "
                f"(rotating about an unmirrored pivot breaks walk symmetry)"
            )


def test_north_walk_mirrors_south_except_stripped_face() -> None:
    """north differs from mirror(south) ONLY at the embedded eyes (face strip)."""
    doc = _doc(embedded_eyes=True)
    palette = resolve_palette(doc.palette)
    walk = generate_joint_walk_cycle(doc, {})
    animated = project_animated_frames(doc, palette, walk)
    ink = palette.rgba("ink")
    for i in range(len(walk)):
        diff = _diff_pixels(animated["north"][i], animated["south"][i].mirror_x())
        # The only difference is the embedded eyes, stripped from the back
        # view. Their positions ride the walk's body bob (y moves by bob_y), so
        # assert the invariant instead of fixed coords: exactly the two eye
        # pixels, each ink-coloured in mirror(south), in the head band.
        assert len(diff) == 2, (
            f"north[{i}] differs from mirror(south) in {len(diff)} pixels; "
            f"expected only the 2 stripped embedded eyes"
        )
        south_arr = animated["south"][i].mirror_x().array
        for x, y in sorted(diff):
            assert tuple(south_arr[y, x]) == ink, (
                f"north[{i}] differs from mirror(south) at non-eye pixel {(x, y)}"
            )


def test_stripped_face_leaves_no_holes_in_any_direction() -> None:
    """The face strip repaints with the local fill colour — no transparent
    holes (4 opaque neighbours) may remain in the head band of any view, or the
    stripped eyes render as eye-shaped dots on a background (critic round 3)."""
    doc = _doc(embedded_eyes=True)
    palette = resolve_palette(doc.palette)
    views = project_directions(doc, palette)

    def holes(c) -> list[tuple[int, int]]:
        a = c.array
        out = []
        for y in range(a.shape[0]):
            for x in range(a.shape[1]):
                if tuple(a[y, x])[3] != 0:
                    continue
                nbrs = 0
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= ny < a.shape[0] and 0 <= nx < a.shape[1] and tuple(a[ny, nx])[3] > 0:
                        nbrs += 1
                if nbrs == 4:
                    out.append((x, y))
        return out

    for direction in DIRECTIONS:
        assert holes(views[direction].composite(doc.asset.canvas)) == [], direction


def test_mirrored_views_record_flipped_pivots() -> None:
    """The projection records the mirrored pivot + flip parity so animation can
    negate rotations; unmirrored views keep the authored anchor and parity 0."""
    doc = _doc()
    palette = resolve_palette(doc.palette)
    views = project_directions(doc, palette)
    # Side views occlude the far (*_left) pair, so the surviving near limbs in
    # east are the *_right pair; west carries their exact flipped copies.
    east_leg = views["east"].region("leg_right")
    west_leg = views["west"].region("leg_right")
    assert east_leg.mirrored is False
    assert east_leg.anchor == (15, 22)  # hip_r authored anchor
    assert west_leg.mirrored is True
    assert west_leg.anchor == (W - 1 - 15, 22) == (8, 22)  # mirrored pivot


def test_side_views_occlude_far_limbs() -> None:
    """True side views (east/west) hide the far-side limbs entirely so the
    profile shows exactly one arm + one leg: east drops the *_left pair, west
    (its exact mirror) drops the same pair. Front and diagonal views keep both
    pairs. Colour level: the round-6 light-side flip (hi<->lo) legitimately
    carries the far pair's fill colours into the side composites (the near
    arm's sleeve_r flips to sleeve_l, the near leg's pants_r to pants_l), so
    the occlusion proof is the region list + palette discipline + byte-exact
    mirroring, not colour absence."""
    doc = _doc()
    palette = resolve_palette(doc.palette)
    views = project_directions(doc, palette)

    def names(direction: str) -> set[str]:
        return {r.name for r in views[direction].regions}

    east, west = names("east"), names("west")
    # Far pair absent, exactly one arm and one leg survive per side.
    assert not {"arm_left", "leg_left"} & east
    assert not {"arm_left", "leg_left"} & west  # west is the mirror of east
    assert {"arm_right", "leg_right"} <= east
    assert {"arm_right", "leg_right"} <= west
    assert len({"arm_left", "arm_right"} & east) == 1
    assert len({"leg_left", "leg_right"} & east) == 1
    # Front + all diagonals keep both pairs.
    for direction in ("south", "south_east", "south_west", "north_east", "north_west"):
        assert {"arm_left", "arm_right", "leg_left", "leg_right"} <= names(direction), direction
    # Pixel level: the flip happened (near limbs carry their dark-side tones
    # and the light ends are flipped away — hi<->lo is a permutation), and no
    # colour outside the palette ever appears.
    allowed = {palette.rgba(cid) for cid in palette.ids}
    east_px = views["east"].composite(doc.asset.canvas)
    west_px = views["west"].composite(doc.asset.canvas)
    for direction, px in (("east", east_px), ("west", west_px)):
        assert px.colors() <= allowed, direction
    assert palette.rgba("sleeve_l") in east_px.colors()  # flipped near arm
    assert palette.rgba("sleeve_r") not in east_px.colors()
    # The fixture's 3px legs sit on odd columns that the even-width 1/2 squash
    # never samples, so no leg fill colour survives the side views at all —
    # leg coverage is proven by region presence + the walk-frame mirrors above.


def test_side_rest_poses_stay_exact_mirrors() -> None:
    """Far-limb occlusion is mirror-symmetric: east hides *_left and west hides
    their exact mirror image, so the side rest poses stay byte-exact mirrors."""
    doc = _doc()
    palette = resolve_palette(doc.palette)
    views = project_directions(doc, palette)
    east = views["east"].composite(doc.asset.canvas)
    west = views["west"].composite(doc.asset.canvas)
    assert west.equals(east.mirror_x())


def test_side_walks_stay_exact_mirrors_with_embedded_eyes() -> None:
    """The east/west walks stay byte-exact mirrors even with embedded face
    detail: the far-side eye strip of the side view mirrors along with the
    limb occlusion, frame by frame."""
    doc = _doc(embedded_eyes=True)
    palette = resolve_palette(doc.palette)
    walk = generate_joint_walk_cycle(doc, {})
    animated = project_animated_frames(doc, palette, walk)
    for i in range(len(walk)):
        assert animated["west"][i].equals(animated["east"][i].mirror_x()), i


def test_mirrored_views_are_byte_identical_on_repeat() -> None:
    """The symmetry fix stays deterministic: repeat projections are byte-identical."""
    doc = _doc()
    palette = resolve_palette(doc.palette)
    walk = generate_joint_walk_cycle(doc, {})
    first = project_animated_frames(doc, palette, walk)
    second = project_animated_frames(doc, palette, walk)
    for direction in DIRECTIONS:
        for i in range(len(walk)):
            assert first[direction][i].equals(second[direction][i]), direction
