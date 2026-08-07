"""Direction projection: determinism, palette discipline, mirror symmetry,
face stripping, silhouette sanity, and occlusion ordering.

The fixture is a warden-style 24x32 layered character built programmatically:
bitmap regions with the input-contract names (torso/head/arm_left/arm_right/
leg_left/leg_right + eyes/hair/shadow), distinct colours per limb so layer
order is pixel-checkable, and an asymmetric mirror-unsafe weapon variant.

The last test regenerates the critic-facing preview artifacts under
`.progress/pieces/direction/` from real renders.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from pixel_forge.domain.palette import ResolvedPalette, resolve_palette
from pixel_forge.errors import ForgeError
from pixel_forge.rendering.canvas import Canvas
from pixel_forge.rendering.compositor import composite, plan_layers
from pixel_forge.rendering.direction import (
    DIRECTIONS,
    BackView,
    discover_roles,
    project_directions,
    project_frames,
)
from pixel_forge.schemas import parse_asset_doc
from pixel_forge.schemas.asset import SpriteAssetBase

ART_DIR = Path(__file__).resolve().parents[2] / ".progress" / "pieces" / "direction"

W, H = 24, 32

_PALETTE = [
    {"id": "ink", "hex": "#14141f"},
    {"id": "skin", "hex": "#e8b88a"},
    {"id": "shirt", "hex": "#3f6db5"},
    {"id": "sleeve_l", "hex": "#c0392b"},
    {"id": "sleeve_r", "hex": "#2e8b57"},
    {"id": "pants_l", "hex": "#7a5230"},
    {"id": "pants_r", "hex": "#5d3d99"},
    {"id": "eye", "hex": "#f8f8f8"},
    {"id": "hair_c", "hex": "#e0a43a"},
    {"id": "shadow_c", "hex": "#0a0a12"},
    {"id": "blade", "hex": "#c0c0c8"},
    {"id": "pack", "hex": "#6b4f2a"},
]


def _bitmap(at: tuple[int, int], key: dict[str, str], rows: list[str]) -> dict[str, Any]:
    return {"op": "bitmap", "at": list(at), "key": key, "rows": rows}


def _regions(*, embedded_eyes: bool = False) -> dict[str, Any]:
    regions = {
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
        # Arms are authored BEHIND the torso (layer 8 < 10): the side-view
        # occlusion reorder must pull the near arm in front of it.
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
                    {"o": "ink", "h": "hair_c"},
                    [".ohhhho.", "ohhhhhho", "oh....ho"],
                )
            ],
        },
        # Face detail as its own region: two symmetric eye pixels.
        "eyes": {
            "anchor": "head",
            "layer": 25,
            "shapes": [_bitmap((-3, -1), {"e": "eye"}, ["e....e"])],
        },
    }
    if embedded_eyes:
        # The demo-character structure instead: no separate face region — the
        # two eyes are ink pixels painted INSIDE a wider head bitmap (world
        # (12,5) and (16,5)), fully surrounded by opaque skin, so they are
        # interior-ink clusters, not outline-ring pixels. The 4px eye spacing
        # on a 14px head mirrors the demo character: both eyes survive the 3/4
        # diagonal squash and both would survive the 1/2 side squash without
        # the far-side strip (landing 2px apart — the defect under test).
        regions["head"]["shapes"] = [
            _bitmap(
                (-6, -4),
                {"o": "ink", "S": "skin"},
                [".oooooooooooo."] + ["oSSSSSSSSSSSSo"] * 6 + [".oooooooooooo."],
            ),
            _bitmap((0, -1), {"e": "ink"}, ["e...e"]),
        ]
        regions.pop("eyes")
    return regions


_ANCHORS: dict[str, Any] = {
    "feet": [12, 31],
    "hip_l": [9, 22],
    "hip_r": [15, 22],
    "torso": [12, 14],
    "shoulder_l": [7, 14],
    "shoulder_r": [16, 14],
    "head": [12, 6],
    "hand_r": [17, 20],
}


def _doc(*, weapon: bool = False, embedded_eyes: bool = False) -> SpriteAssetBase:
    regions = _regions(embedded_eyes=embedded_eyes)
    if weapon:
        # Asymmetric, mirror-unsafe: a blade held out to screen-right.
        regions["weapon"] = {
            "anchor": "hand_r",
            "layer": 30,
            "mirror_safe": False,
            "shapes": [
                _bitmap(
                    (0, -7),
                    {"B": "blade", "o": "ink"},
                    ["..BB", "..B.", "..B.", "..B.", "oBBB", "..o."],
                )
            ],
        }
    doc = parse_asset_doc(
        {
            "schema_version": 1,
            "asset": {"id": "scout", "type": "character", "canvas": [W, H]},
            "palette": {"id": "p", "colors": _PALETTE},
            "directions": ["south"],
            "anchors": _ANCHORS,
            "regions": regions,
            "animations": {},
            "export": {},
            "validation": {},
        }
    )
    assert isinstance(doc, SpriteAssetBase)
    return doc


@pytest.fixture
def doc() -> SpriteAssetBase:
    return _doc()


@pytest.fixture
def palette(doc: SpriteAssetBase) -> ResolvedPalette:
    return resolve_palette(doc.palette)


# --- ramp shading (round 6) ---------------------------------------------------

# A character whose materials are real hi/mid/lo ramps, authored with a
# screen-left light source (light fills the left edge, shade the right), so the
# side-view light re-orientation and the diagonal far-limb darkening are
# pixel-checkable. Colours deliberately match the demo character's ramps.
_RAMP_PALETTE = [
    {"id": "ink", "hex": "#14141f"},
    {"id": "shirt_hi", "hex": "#76a8e0"},
    {"id": "shirt_mid", "hex": "#5482c4"},
    {"id": "shirt_lo", "hex": "#3a5e9e"},
    {"id": "pants_hi", "hex": "#6c6082"},
    {"id": "pants_mid", "hex": "#544868"},
    {"id": "pants_lo", "hex": "#3c324e"},
    {"id": "skin_hi", "hex": "#f4d6b6"},
    {"id": "skin_mid", "hex": "#e0b694"},
    {"id": "skin_lo", "hex": "#c49674"},
    {"id": "eye", "hex": "#f8f8f8"},
    {"id": "hair_c", "hex": "#e0a43a"},
    {"id": "shadow_c", "hex": "#0a0a12"},
]


def _doc_ramps() -> SpriteAssetBase:
    """Scout clone with ramp materials; torso/head left-lit, legs pants_mid."""
    regions = {
        "shadow": {
            "anchor": "feet",
            "layer": 0,
            "shapes": [{"op": "ellipse", "color": "shadow_c", "at": [-8, -2], "size": [16, 3]}],
        },
        "leg_left": {
            "anchor": "hip_l",
            "layer": 5,
            "shapes": [_bitmap((-2, 0), {"o": "ink", "L": "pants_mid"}, ["oLLo"] * 7 + ["oooo"])],
        },
        "leg_right": {
            "anchor": "hip_r",
            "layer": 5,
            "shapes": [_bitmap((-2, 0), {"o": "ink", "L": "pants_mid"}, ["oLLo"] * 7 + ["oooo"])],
        },
        "arm_left": {
            "anchor": "shoulder_l",
            "layer": 8,
            "shapes": [_bitmap((-1, 0), {"A": "shirt_mid", "o": "ink"}, ["AAAA"] * 8 + ["oooo"])],
        },
        "arm_right": {
            "anchor": "shoulder_r",
            "layer": 8,
            "shapes": [_bitmap((-1, 0), {"A": "shirt_mid", "o": "ink"}, ["AAAA"] * 8 + ["oooo"])],
        },
        "torso": {
            "anchor": "torso",
            "layer": 10,
            "shapes": [
                _bitmap(
                    (-5, -4),
                    {"o": "ink", "H": "shirt_hi", "M": "shirt_mid", "L": "shirt_lo"},
                    [
                        "oooooooooo",
                        "HHHMMMMLLL",
                        "HHHMMMMLLL",
                        "HHHMMMMLLL",
                        "HHHMMMMLLL",
                        "HHHMMMMLLL",
                        "HHHMMMMLLL",
                        "HHHMMMMLLL",
                        "HHHMMMMLLL",
                        ".oooooooo.",
                    ],
                )
            ],
        },
        "head": {
            "anchor": "head",
            "layer": 20,
            "shapes": [
                _bitmap(
                    (-4, -4),
                    {"o": "ink", "H": "skin_hi", "M": "skin_mid", "L": "skin_lo"},
                    [
                        ".oooooo.",
                        "oHHMMLLo",
                        "oHHMMLLo",
                        "oHHMMLLo",
                        "oHHMMLLo",
                        "oHHMMLLo",
                        "oHHMMLLo",
                        ".oooooo.",
                    ],
                )
            ],
        },
        "hair": {
            "anchor": "head",
            "layer": 22,
            "shapes": [
                _bitmap(
                    (-4, -5),
                    {"o": "ink", "h": "hair_c"},
                    [".ohhhho.", "ohhhhhho", "oh....ho"],
                )
            ],
        },
        "eyes": {
            "anchor": "head",
            "layer": 25,
            "shapes": [_bitmap((-3, -1), {"e": "eye"}, ["e....e"])],
        },
    }
    doc = parse_asset_doc(
        {
            "schema_version": 1,
            "asset": {"id": "scout", "type": "character", "canvas": [W, H]},
            "palette": {"id": "p", "colors": _RAMP_PALETTE},
            "directions": ["south"],
            "anchors": _ANCHORS,
            "regions": regions,
            "animations": {},
            "export": {},
            "validation": {},
        }
    )
    assert isinstance(doc, SpriteAssetBase)
    return doc


def _region_band_x(canvas: Canvas, rgba: tuple[int, int, int, int]) -> list[int]:
    """x-columns where `rgba` appears in a (canvas-sized) region canvas."""
    a = canvas.array
    out = []
    for y in range(a.shape[0]):
        for x in range(a.shape[1]):
            if tuple(a[y, x]) == rgba:
                out.append(x)
    return out


def test_ramp_inference_finds_demo_ramps_and_excludes_near_blacks() -> None:
    """The hex-only ramp inference clusters the demo's materials and refuses
    the near-black outline/detail colours (round-5 biggest gap, round 6)."""
    from pixel_forge.rendering.direction import _infer_ramps

    doc = _doc_ramps()
    palette = resolve_palette(doc.palette)
    ramps = _infer_ramps(palette)
    families = ramps.families
    # Each material is one family with the expected hi/lo endpoints.
    assert any("shirt_hi" in fam and "shirt_lo" in fam for fam in families), families
    assert any("pants_hi" in fam and "pants_lo" in fam for fam in families), families
    assert any("skin_hi" in fam and "skin_lo" in fam for fam in families), families
    assert not any("ink" in fam for fam in families), "ink must never be a ramp member"


def test_ramp_flat_palette_inference_is_empty() -> None:
    """A flat palette (the warden fixture, one colour per part) yields no ramp
    families — the shading pass is a byte-identical no-op for flat art."""
    from pixel_forge.rendering.direction import _infer_ramps

    doc = _doc()
    palette = resolve_palette(doc.palette)
    assert _infer_ramps(palette).families == ()


def test_ramp_side_view_reorients_light_to_near_side() -> None:
    """East flips hi<->lo so the light leaves the character's back: the torso's
    lightest shirt colour must sit RIGHT of its darkest in the profile, the
    opposite of the authored (screen-left-light) front."""
    doc = _doc_ramps()
    palette = resolve_palette(doc.palette)
    views = project_directions(doc, palette)

    def hi_lo_x(direction: str) -> tuple[list[int], list[int]]:
        torso = next(r for r in views[direction].regions if r.name == "torso")
        hi = _region_band_x(torso.canvas, palette.rgba("shirt_hi"))
        lo = _region_band_x(torso.canvas, palette.rgba("shirt_lo"))
        return hi, lo

    south_hi, south_lo = hi_lo_x("south")
    assert south_hi and south_lo
    assert min(south_hi) < max(south_lo)  # authored front: light left, shade right

    east_hi, east_lo = hi_lo_x("east")
    assert east_hi and east_lo
    assert min(east_hi) > max(east_lo)  # profile: light near (right), shade far (left)

    west_hi, west_lo = hi_lo_x("west")
    assert west_hi and west_lo
    assert min(west_hi) < max(west_lo)  # west mirror: light near (left)


def test_ramp_diagonal_shades_far_limb_one_step_darker() -> None:
    """Every diagonal shades the camera-FAR leg one ramp step darker (mid -> lo)
    while the near leg keeps the authored mid tone. The dark leg's screen side
    depends on the view: front diagonals shade the side opposite the facing
    (SE left, SW right); back diagonals shade the side being turned away toward
    (NE right, NW left). Mirrored pairs stay byte-exact mirrors."""
    doc = _doc_ramps()
    palette = resolve_palette(doc.palette)
    views = project_directions(doc, palette)
    mid, lo = palette.rgba("pants_mid"), palette.rgba("pants_lo")
    w = doc.asset.canvas[0]

    def side_colours(direction: str, side: str) -> set[tuple[int, int, int, int]]:
        c = views[direction].composite(doc.asset.canvas)
        out = set()
        for y in range(c.height):
            for x in range(c.width):
                if (x < w / 2) == (side == "left"):
                    rgba = tuple(c.array[y, x])
                    if rgba in (mid, lo):
                        out.add(rgba)
        return out

    # far side (dark) / near side (mid), per direction.
    expected = {
        "south_east": ("left", "right"),
        "south_west": ("right", "left"),
        "north_east": ("right", "left"),
        "north_west": ("left", "right"),
    }
    for direction, (far_side, near_side) in expected.items():
        assert lo in side_colours(direction, far_side), direction
        assert mid not in side_colours(direction, far_side), direction
        assert mid in side_colours(direction, near_side), direction
        assert lo not in side_colours(direction, near_side), direction

    # Front keeps the authored tone on both legs.
    south = views["south"].composite(doc.asset.canvas)
    assert lo not in set(south.colors())
    # Mirrored pairs stay exact mirrors even with the shading.
    for a, b in (("south_west", "south_east"), ("north_west", "north_east")):
        assert (
            views[a]
            .composite(doc.asset.canvas)
            .equals(views[b].composite(doc.asset.canvas).mirror_x())
        )


def test_ramp_diagonals_reorient_torso_light_to_near_side() -> None:
    """Diagonals flip the BODY light (torso hi<->lo) so the chest faces the
    light like the side views, while the limbs keep the far/near depth shading
    (the flip must NOT cancel the far-leg darkening — round-7 critic: SE/SW
    torso lighting was inconsistent, far side lit)."""
    doc = _doc_ramps()
    palette = resolve_palette(doc.palette)
    views = project_directions(doc, palette)
    hi, lo = palette.rgba("shirt_hi"), palette.rgba("shirt_lo")
    w = doc.asset.canvas[0]

    def torso_sides(direction: str) -> tuple[list[int], list[int]]:
        c = views[direction].composite(doc.asset.canvas)
        hi_x, lo_x = [], []
        for y in range(c.height):
            for x in range(c.width):
                rgba = tuple(c.array[y, x])
                if rgba == hi:
                    hi_x.append(x)
                elif rgba == lo:
                    lo_x.append(x)
        return hi_x, lo_x

    # SE (facing right): light on the near/chest (right), shade on the far (left).
    se_hi, se_lo = torso_sides("south_east")
    assert se_hi and se_lo
    assert min(se_hi) > w / 2 > max(se_lo), (se_hi, se_lo)
    # SW is the mirror: light on the left.
    sw_hi, sw_lo = torso_sides("south_west")
    assert sw_hi and sw_lo
    assert min(sw_hi) < w / 2 < max(sw_lo), (sw_hi, sw_lo)
    # The far/near leg shading is untouched (the flip excludes limbs).
    mid, plo = palette.rgba("pants_mid"), palette.rgba("pants_lo")

    def leg_sides(direction: str) -> tuple[list[int], list[int]]:
        c = views[direction].composite(doc.asset.canvas)
        mid_x, lo_x = [], []
        for y in range(c.height):
            for x in range(c.width):
                rgba = tuple(c.array[y, x])
                if rgba == mid:
                    mid_x.append(x)
                elif rgba == plo:
                    lo_x.append(x)
        return mid_x, lo_x

    se_mid, se_plo = leg_sides("south_east")
    assert se_mid and se_plo
    assert max(se_plo) < w / 2 < min(se_mid)  # far leg (left) dark, near leg (right) mid


def test_ramp_side_views_stay_exact_mirrors() -> None:
    """The light re-orientation is mirror-symmetric: west == mirror(east)."""
    doc = _doc_ramps()
    palette = resolve_palette(doc.palette)
    views = project_directions(doc, palette)
    east = views["east"].composite(doc.asset.canvas)
    west = views["west"].composite(doc.asset.canvas)
    assert west.equals(east.mirror_x())


def _rgba(palette: ResolvedPalette, color_id: str) -> tuple[int, int, int, int]:
    return palette.rgba(color_id)


# --- role discovery ----------------------------------------------------------


def test_discover_roles_contract_names(doc: SpriteAssetBase) -> None:
    roles = discover_roles(doc)
    assert roles.torso == "torso"
    assert roles.head == "head"
    assert roles.arm_left == "arm_left"
    assert roles.arm_right == "arm_right"
    assert roles.leg_left == "leg_left"
    assert roles.leg_right == "leg_right"
    assert roles.face == frozenset({"eyes"})
    assert roles.static == frozenset({"shadow"})


def test_discover_roles_warden_style_names() -> None:
    # The warden example's `left_arm`/`right_arm` naming must classify the same.
    regions = _regions()
    regions["left_arm"] = regions.pop("arm_left")
    regions["right_arm"] = regions.pop("arm_right")
    doc = parse_asset_doc(
        {
            "schema_version": 1,
            "asset": {"id": "scout", "type": "character", "canvas": [W, H]},
            "palette": {"id": "p", "colors": _PALETTE},
            "directions": ["south"],
            "anchors": _ANCHORS,
            "regions": regions,
            "animations": {},
            "export": {},
            "validation": {},
        }
    )
    assert isinstance(doc, SpriteAssetBase)
    roles = discover_roles(doc)
    assert roles.arm_left == "left_arm"
    assert roles.arm_right == "right_arm"


# --- structure and determinism -----------------------------------------------


def test_all_eight_directions_present(doc: SpriteAssetBase, palette: ResolvedPalette) -> None:
    views = project_directions(doc, palette)
    assert tuple(views) == DIRECTIONS
    assert views["west"].mirrored_from == "east"
    assert views["south_west"].mirrored_from == "south_east"
    assert views["north_west"].mirrored_from == "north_east"
    assert views["north"].mirrored_from == "south"
    assert views["south"].mirrored_from is None


def test_determinism_byte_identical(
    doc: SpriteAssetBase, palette: ResolvedPalette, tmp_path: Path
) -> None:
    first = project_frames(doc, palette)
    second = project_frames(doc, palette)
    assert first.keys() == second.keys()
    for direction in DIRECTIONS:
        assert first[direction].equals(second[direction]), direction
        a = tmp_path / f"a_{direction}.png"
        b = tmp_path / f"b_{direction}.png"
        first[direction].save_png(a)
        second[direction].save_png(b)
        assert a.read_bytes() == b.read_bytes(), direction


def test_palette_discipline(doc: SpriteAssetBase, palette: ResolvedPalette) -> None:
    allowed = {palette.rgba(color_id) for color_id in palette.ids}
    for direction, canvas in project_frames(doc, palette).items():
        assert canvas.colors() <= allowed, direction


def test_requires_regions(palette: ResolvedPalette) -> None:
    empty = parse_asset_doc(
        {
            "schema_version": 1,
            "asset": {"id": "ghost", "type": "character", "canvas": [W, H]},
            "palette": {"id": "p", "colors": _PALETTE},
            "directions": ["south"],
            "anchors": _ANCHORS,
            "regions": {},
            "animations": {},
            "export": {},
            "validation": {},
        }
    )
    assert isinstance(empty, SpriteAssetBase)
    with pytest.raises(ForgeError, match="requires drawn regions"):
        project_directions(empty, palette)


# --- south reproduces the authored front -------------------------------------


def test_south_reproduces_authored_front(doc: SpriteAssetBase, palette: ResolvedPalette) -> None:
    layers = plan_layers(doc, doc.regions, doc.anchors, {}, palette)
    authored = composite(doc.asset.canvas, layers, palette)
    projected = project_frames(doc, palette)["south"]
    assert projected.equals(authored)


# --- mirror symmetry ----------------------------------------------------------


def test_opposite_directions_are_exact_mirrors(
    doc: SpriteAssetBase, palette: ResolvedPalette
) -> None:
    frames = project_frames(doc, palette)
    for a, b in (("east", "west"), ("south_east", "south_west"), ("north_east", "north_west")):
        assert frames[a].mirror_x().equals(frames[b]), (a, b)


def test_mirror_unsafe_region_is_never_flipped(palette: ResolvedPalette) -> None:
    doc = _doc(weapon=True)
    pal = resolve_palette(doc.palette)
    views = project_directions(doc, pal)
    east_weapon = views["east"].region("weapon").canvas
    west_weapon = views["west"].region("weapon").canvas
    # The weapon's content is asymmetric (blade points screen-right)...
    base = composite(
        doc.asset.canvas,
        plan_layers(doc, {"weapon": doc.regions["weapon"]}, doc.anchors, {}, pal),
        pal,
    )
    assert not base.equals(base.mirror_x())
    # ...and both side views carry it unflipped (squash is content-independent),
    # so east and west show the identical un-mirrored weapon canvas.
    assert east_weapon.equals(west_weapon)
    # Column-dropping squash may thin the ink, but the blade survives and no
    # foreign colour ever appears.
    assert _rgba(pal, "blade") in east_weapon.colors()
    assert east_weapon.colors() <= {_rgba(pal, "blade"), _rgba(pal, "ink")}


# --- back view ----------------------------------------------------------------


def test_back_strips_face_detail(doc: SpriteAssetBase, palette: ResolvedPalette) -> None:
    frames = project_frames(doc, palette)
    eye = _rgba(palette, "eye")
    for direction in ("north", "north_east", "north_west"):
        assert eye not in frames[direction].colors(), direction
    for direction in ("south", "south_east", "east"):
        assert eye in frames[direction].colors(), direction
    # Hair is not face detail: it stays on the back of the head.
    assert _rgba(palette, "hair_c") in frames["north"].colors()


def test_north_is_front_mirror_minus_face(doc: SpriteAssetBase, palette: ResolvedPalette) -> None:
    frames = project_frames(doc, palette)
    south = frames["south"].mirror_x()
    north = frames["north"]
    eye = _rgba(palette, "eye")
    for y in range(H):
        for x in range(W):
            s_px = south.get_pixel(x, y)
            n_px = north.get_pixel(x, y)
            if s_px == eye:
                # Stripped: whatever the head/hair underneath drew shows instead.
                assert n_px != eye
            else:
                assert n_px == s_px


def test_front_diagonals_keep_the_near_eye(doc: SpriteAssetBase, palette: ResolvedPalette) -> None:
    """Regression: the 3/4 squash made the near eye's source column unreachable,
    so the far eye survived and the character looked cross-eyed. The surviving
    face pixel on a right-facing diagonal must sit on the character's near
    (facing) side of the head centre; the left-facing diagonal the mirror."""
    frames = project_frames(doc, palette)
    eye = _rgba(palette, "eye")
    head_bbox = project_directions(doc, palette)["south"].region("head").canvas.bbox()
    assert head_bbox is not None
    centre_x = (head_bbox[0] + head_bbox[2] - 1) // 2

    def eye_xs(direction: str) -> list[int]:
        arr = frames[direction].array
        _ys, xs = (arr == eye).all(axis=2).nonzero()
        return sorted(xs.tolist())

    # SE faces down-right: the near eye is on the right of the head centre.
    se = eye_xs("south_east")
    assert se, "south_east must keep an eye"
    assert all(x > centre_x for x in se), (se, centre_x)
    # SW is the exact mirror: the near eye sits left of centre.
    sw = eye_xs("south_west")
    assert sw, "south_west must keep an eye"
    assert all(x < centre_x for x in sw), (sw, centre_x)
    # Side views keep the same convention (near eye on the facing side).
    e = eye_xs("east")
    w = eye_xs("west")
    assert all(x > centre_x for x in e), (e, centre_x)
    assert all(x < centre_x for x in w), (w, centre_x)


def test_supplied_back_view_wins(doc: SpriteAssetBase, palette: ResolvedPalette) -> None:
    pack_canvas = Canvas(W, H)
    # A backpack block where the torso sits, in a colour the front never uses.
    for y in range(11, 18):
        for x in range(9, 15):
            pack_canvas.set_pixel(x, y, _rgba(palette, "pack"))
    back = BackView(regions={"torso": pack_canvas})
    frames = project_frames(doc, palette, back=back)
    pack = _rgba(palette, "pack")
    assert pack in frames["north"].colors()
    assert pack in frames["north_east"].colors()  # back diagonal squashes the real back
    assert pack in frames["north_west"].colors()
    assert pack not in frames["south"].colors()
    # Face detail is still stripped, and the supplied back is not a mirror.
    assert _rgba(palette, "eye") not in frames["north"].colors()
    assert not frames["north"].equals(frames["south"].mirror_x())
    views = project_directions(doc, palette, back=back)
    assert views["north"].mirrored_from is None


# --- embedded face detail (painted inside the head bitmap) --------------------


def _interior_ink(canvas: Canvas, ink: tuple[int, int, int, int]) -> set[tuple[int, int]]:
    """Pixels of `ink` colour whose four orthogonal neighbours are all opaque —
    the same 'embedded face feature' definition the projection strips on."""
    arr = canvas.array
    h, w = arr.shape[:2]
    out: set[tuple[int, int]] = set()
    for y in range(h):
        for x in range(w):
            if tuple(int(v) for v in arr[y, x]) != ink:
                continue
            if all(
                0 <= y + dy < h and 0 <= x + dx < w and arr[y + dy, x + dx][3] != 0
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1))
            ):
                out.add((x, y))
    return out


def test_embedded_face_fixture_is_interior_ink(palette: ResolvedPalette) -> None:
    """Sanity: the fixture's eyes are interior-ink clusters inside the head
    bitmap (both fully surrounded by opaque skin), and no outline pixel is —
    the demo character's structure."""
    doc = _doc(embedded_eyes=True)
    pal = resolve_palette(doc.palette)
    ink = _rgba(pal, "ink")
    head = project_directions(doc, pal)["south"].region("head").canvas
    assert _interior_ink(head, ink) == {(12, 5), (16, 5)}


def test_back_views_strip_embedded_face_features(palette: ResolvedPalette) -> None:
    """Back-facing views must not show eyes painted INTO the head bitmap: the
    head region canvas of north / north_east / north_west carries zero
    interior-ink pixels (the outline ring still renders, but no embedded face
    detail)."""
    doc = _doc(embedded_eyes=True)
    pal = resolve_palette(doc.palette)
    ink = _rgba(pal, "ink")
    views = project_directions(doc, pal)
    for direction in ("north", "north_east", "north_west"):
        interior = _interior_ink(views[direction].region("head").canvas, ink)
        assert not interior, (direction, interior)


def test_side_views_keep_only_the_near_embedded_eye(palette: ResolvedPalette) -> None:
    """A true profile shows exactly the near-side eye: east (facing screen-right)
    keeps the right-of-centre eye and west keeps its exact mirror — one interior
    ink cluster each, on the facing side of the head's centre axis. Front
    diagonals are untouched: both eyes survive the 3/4 squash."""
    doc = _doc(embedded_eyes=True)
    pal = resolve_palette(doc.palette)
    ink = _rgba(pal, "ink")
    views = project_directions(doc, pal)
    head_bbox = views["south"].region("head").canvas.bbox()
    assert head_bbox is not None
    centre2 = head_bbox[0] + head_bbox[2] - 1  # doubled head centre axis
    east = _interior_ink(views["east"].region("head").canvas, ink)
    west = _interior_ink(views["west"].region("head").canvas, ink)
    assert len(east) == 1, east
    assert len(west) == 1, west
    ((ex, ey),) = east
    ((wx, wy),) = west
    assert 2 * ex > centre2, (east, centre2)  # near side for east-facing
    assert 2 * wx < centre2, (west, centre2)  # near side for west-facing
    assert (wx, wy) == (W - 1 - ex, ey)  # west is the exact mirror of east
    for direction in ("south", "south_east", "south_west"):
        interior = _interior_ink(views[direction].region("head").canvas, ink)
        assert len(interior) == 2, (direction, interior)


def test_south_keeps_embedded_face_unchanged(palette: ResolvedPalette) -> None:
    """south reproduces the authored front byte-for-byte even with embedded
    eyes: the front path is never stripped."""
    doc = _doc(embedded_eyes=True)
    pal = resolve_palette(doc.palette)
    frames = project_frames(doc, pal)
    layers = plan_layers(doc, doc.regions, doc.anchors, {}, pal)
    authored = composite(doc.asset.canvas, layers, pal)
    assert frames["south"].equals(authored)
    head = project_directions(doc, pal)["south"].region("head").canvas
    assert _interior_ink(head, _rgba(pal, "ink")) == {(12, 5), (16, 5)}


def test_embedded_face_projection_is_deterministic(palette: ResolvedPalette) -> None:
    """project_directions on an embedded-eye doc called twice returns
    byte-identical views in every direction."""
    doc = _doc(embedded_eyes=True)
    pal = resolve_palette(doc.palette)
    first = project_frames(doc, pal)
    second = project_frames(doc, pal)
    for direction in DIRECTIONS:
        assert first[direction].equals(second[direction]), direction


# --- silhouette sanity ---------------------------------------------------------


def test_side_silhouette_is_narrower_but_present(
    doc: SpriteAssetBase, palette: ResolvedPalette
) -> None:
    frames = project_frames(doc, palette)
    front_bbox = frames["south"].bbox()
    side_bbox = frames["east"].bbox()
    assert front_bbox is not None and side_bbox is not None
    front_w = front_bbox[2] - front_bbox[0]
    side_w = side_bbox[2] - side_bbox[0]
    assert 0 < side_w < front_w
    # Overall height is preserved (projection never moves pixels vertically).
    assert (side_bbox[3] - side_bbox[1]) == (front_bbox[3] - front_bbox[1])


def test_per_region_heights_and_stacking_preserved(
    doc: SpriteAssetBase, palette: ResolvedPalette
) -> None:
    views = project_directions(doc, palette)
    front = views["south"]
    for direction in DIRECTIONS:
        view = views[direction]
        # Side views occlude the far (*_left) pair; check the surviving near
        # limbs there and the usual pair everywhere else.
        if direction in ("east", "west"):
            names = ("torso", "head", "arm_right", "leg_right")
        else:
            names = ("torso", "head", "arm_left", "leg_right")
        for name in names:
            f_bbox = front.region(name).canvas.bbox()
            d_bbox = view.region(name).canvas.bbox()
            assert f_bbox is not None and d_bbox is not None
            # Same vertical extent and position in every direction (±0 <= ±2).
            assert abs((d_bbox[3] - d_bbox[1]) - (f_bbox[3] - f_bbox[1])) <= 2
            assert abs(d_bbox[1] - f_bbox[1]) <= 2
        # Head stays atop the torso in every direction.
        head_bbox = view.region("head").canvas.bbox()
        torso_bbox = view.region("torso").canvas.bbox()
        assert head_bbox is not None and torso_bbox is not None
        assert head_bbox[3] <= torso_bbox[1] + 1, direction


# --- side-view limb occlusion ---------------------------------------------------


def _overlap_color(view_canvas: Canvas, a: Canvas, b: Canvas) -> set[tuple[int, int, int, int]]:
    """Colours of the composited view at pixels where both region canvases are opaque."""
    out: set[tuple[int, int, int, int]] = set()
    for y in range(H):
        for x in range(W):
            if a.get_pixel(x, y)[3] == 255 and b.get_pixel(x, y)[3] == 255:
                out.add(view_canvas.get_pixel(x, y))
    return out


def test_side_view_limb_occlusion(doc: SpriteAssetBase, palette: ResolvedPalette) -> None:
    views = project_directions(doc, palette)
    east = views["east"]
    names = [region.name for region in east.regions]
    # True profile: the far-side pair is occluded entirely — only the near
    # arm/leg survive, drawn in front of the torso.
    assert "arm_left" not in names
    assert "leg_left" not in names
    assert names.index("torso") < names.index("arm_right")
    assert names.index("torso") < names.index("leg_right")

    canvas = east.composite(doc.asset.canvas)
    torso = east.region("torso").canvas
    near_arm = east.region("arm_right").canvas
    shirt = _rgba(palette, "shirt")
    # Where the near arm overlaps the torso, the near arm wins (sleeve_r).
    near_overlap = _overlap_color(canvas, near_arm, torso)
    assert near_overlap, "expected the squashed near arm to overlap the torso"
    assert _rgba(palette, "sleeve_r") in near_overlap
    assert shirt not in near_overlap
    # Far-side colours never reach the composite.
    assert _rgba(palette, "sleeve_l") not in canvas.colors()
    assert _rgba(palette, "pants_l") not in canvas.colors()


def test_diagonal_squash_between_front_and_side(
    doc: SpriteAssetBase, palette: ResolvedPalette
) -> None:
    frames = project_frames(doc, palette)
    widths: dict[str, int] = {}
    for direction in ("south", "south_east", "east"):
        bbox = frames[direction].bbox()
        assert bbox is not None
        widths[direction] = bbox[2] - bbox[0]
    assert widths["east"] < widths["south_east"] < widths["south"]


# --- preview artifacts for the critic ------------------------------------------


def test_preview_artifacts(doc: SpriteAssetBase, palette: ResolvedPalette) -> None:
    """Regenerate `.progress/pieces/direction/` from real renders: one x4 PNG per
    direction, a 3x3 compass contact sheet, and a front-vs-back pair."""
    frames = project_frames(doc, palette)
    ART_DIR.mkdir(parents=True, exist_ok=True)

    for direction, canvas in frames.items():
        canvas.scale(4).save_png(ART_DIR / f"{direction}.png")

    gap = 2
    sheet = Canvas(3 * W + 4 * gap, 3 * H + 4 * gap)
    layout = (
        ("north_west", "north", "north_east"),
        ("west", None, "east"),
        ("south_west", "south", "south_east"),
    )
    for row, directions in enumerate(layout):
        for col, direction in enumerate(directions):
            if direction is None:
                continue
            sheet.blit(frames[direction], (gap + col * (W + gap), gap + row * (H + gap)))
    sheet.scale(4).save_png(ART_DIR / "contact_sheet.png")

    pair = Canvas(2 * W + 3 * gap, H + 2 * gap)
    pair.blit(frames["south"], (gap, gap))
    pair.blit(frames["north"], (2 * gap + W, gap))
    pair.scale(4).save_png(ART_DIR / "front_vs_back.png")

    for name in (*DIRECTIONS, "contact_sheet", "front_vs_back"):
        path = ART_DIR / f"{name}.png"
        assert path.is_file(), name
        with Image.open(path) as img:
            assert img.size[0] % 4 == 0 and img.size[1] % 4 == 0
