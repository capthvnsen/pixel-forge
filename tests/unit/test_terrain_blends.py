from __future__ import annotations

from typing import Any

from pixel_forge.domain.palette import resolve_palette
from pixel_forge.rendering.canvas import RGBA, Canvas
from pixel_forge.rendering.terrain import check_transition_blends
from pixel_forge.schemas import TerrainAsset, parse_asset_doc

GRASS: RGBA = (76, 154, 42, 255)
DIRT: RGBA = (138, 90, 52, 255)
EDGE: RGBA = (46, 36, 31, 255)


def _terrain_doc(transitions: list[dict[str, Any]]) -> TerrainAsset:
    data: dict[str, Any] = {
        "schema_version": 1,
        "asset": {"id": "demo", "type": "terrain", "canvas": [16, 16]},
        "palette": {
            "id": "p",
            "colors": [
                {"id": "grass", "hex": "#4c9a2a"},
                {"id": "dirt", "hex": "#8a5a34"},
                {"id": "edge", "hex": "#2e241f"},
            ],
        },
        "export": {},
        "validation": {},
        "tiles": {"gd_n": {"size": [16, 16], "regions": {}, "anchors": {}, "terrain": "dirt"}},
        "terrain_sets": {},
        "transitions": transitions,
    }
    doc = parse_asset_doc(data)
    assert isinstance(doc, TerrainAsset)
    return doc


def _ringed() -> Canvas:
    c = Canvas(16, 16)
    c.draw_rect((0, 0), (16, 16), EDGE, fill=True)
    return c


def _hard_north_tile() -> Canvas:
    """Dirt tile with a straight full-width grass band at the top: a hard line."""
    c = _ringed()
    c.draw_rect((1, 1), (14, 14), DIRT, fill=True)
    c.draw_rect((1, 1), (14, 5), GRASS, fill=True)  # rows 1..5 grass, row 6+ dirt
    return c


def _blended_north_tile() -> Canvas:
    """Same, but the band's lower edge steps across two rows (a 2px blend zone)."""
    c = _ringed()
    c.draw_rect((1, 1), (14, 14), DIRT, fill=True)
    c.draw_rect((1, 1), (14, 5), GRASS, fill=True)  # rows 1..5
    c.draw_rect((1, 6), (11, 1), GRASS, fill=True)  # row 6, cols 1..11
    c.draw_rect((3, 7), (6, 1), GRASS, fill=True)  # row 7, cols 3..8
    return c


def _hard_west_tile() -> Canvas:
    """Dirt tile with a straight full-height grass band on the west: a hard line."""
    c = _ringed()
    c.draw_rect((1, 1), (14, 14), DIRT, fill=True)
    c.draw_rect((1, 1), (5, 14), GRASS, fill=True)  # cols 1..5 grass
    return c


def _square_corner_tile() -> Canvas:
    """NE corner: a straight 6x6 grass square — one mixed row and one mixed column."""
    c = _ringed()
    c.draw_rect((1, 1), (14, 14), DIRT, fill=True)
    c.draw_rect((9, 1), (6, 6), GRASS, fill=True)  # cols 9..14, rows 1..6
    return c


def _stepped_corner_tile() -> Canvas:
    """NE corner: a stepped grass staircase — the boundary wanders 2px on both axes."""
    c = _ringed()
    c.draw_rect((1, 1), (14, 14), DIRT, fill=True)
    c.draw_rect((9, 1), (6, 4), GRASS, fill=True)  # rows 1..4, cols 9..14
    c.draw_rect((10, 5), (4, 1), GRASS, fill=True)  # row 5, cols 10..13
    c.draw_rect((11, 6), (2, 1), GRASS, fill=True)  # row 6, cols 11..12
    return c


def _single_material_tile() -> Canvas:
    c = _ringed()
    c.draw_rect((1, 1), (14, 14), DIRT, fill=True)  # no grass patch at all
    return c


def test_check_transition_blends_flags_straight_edge_as_hard() -> None:
    doc = _terrain_doc(
        [{"from_terrain": "grass", "to_terrain": "dirt", "tile_id": "gd_n", "mask": "N"}]
    )
    reports = check_transition_blends(
        doc, {"gd_n": _hard_north_tile()}, resolve_palette(doc.palette)
    )
    assert len(reports) == 1
    report = reports[0]
    assert report.is_hard is True
    assert report.blend_width == 0
    assert report.materials == 2
    assert "blend zone" in report.note


def test_check_transition_blends_accepts_stepped_edge() -> None:
    doc = _terrain_doc(
        [{"from_terrain": "grass", "to_terrain": "dirt", "tile_id": "gd_n", "mask": "N"}]
    )
    reports = check_transition_blends(
        doc, {"gd_n": _blended_north_tile()}, resolve_palette(doc.palette)
    )
    assert len(reports) == 1
    report = reports[0]
    assert report.is_hard is False
    assert report.blend_width == 2
    assert report.note == ""


def test_check_transition_blends_flags_straight_vertical_edge() -> None:
    doc = _terrain_doc(
        [{"from_terrain": "grass", "to_terrain": "dirt", "tile_id": "gd_n", "mask": "W"}]
    )
    reports = check_transition_blends(
        doc, {"gd_n": _hard_west_tile()}, resolve_palette(doc.palette)
    )
    assert len(reports) == 1
    assert reports[0].is_hard is True
    assert reports[0].blend_width == 0


def test_check_transition_blends_south_patch_measures_the_top_edge() -> None:
    # A south patch's boundary is its TOP edge; a straight band is hard, a
    # stepped one is not — the low-side extent must be measured, not the max.
    straight_doc = _terrain_doc(
        [{"from_terrain": "grass", "to_terrain": "dirt", "tile_id": "gd_n", "mask": "S"}]
    )
    straight = Canvas(16, 16)
    straight.draw_rect((0, 0), (16, 16), EDGE, fill=True)
    straight.draw_rect((1, 1), (14, 14), DIRT, fill=True)
    straight.draw_rect((1, 11), (14, 4), GRASS, fill=True)  # rows 11..14 grass
    hard = check_transition_blends(
        straight_doc, {"gd_n": straight}, resolve_palette(straight_doc.palette)
    )[0]
    assert hard.is_hard is True
    assert hard.blend_width == 0

    stepped_doc = _terrain_doc(
        [{"from_terrain": "grass", "to_terrain": "dirt", "tile_id": "gd_n", "mask": "S"}]
    )
    stepped = Canvas(16, 16)
    stepped.draw_rect((0, 0), (16, 16), EDGE, fill=True)
    stepped.draw_rect((1, 1), (14, 14), DIRT, fill=True)
    stepped.draw_rect((1, 11), (14, 4), GRASS, fill=True)  # rows 11..14
    stepped.draw_rect((1, 10), (11, 1), GRASS, fill=True)  # row 10, cols 1..11
    stepped.draw_rect((3, 9), (7, 1), GRASS, fill=True)  # row 9, cols 3..9
    stepped.draw_rect((5, 8), (2, 1), GRASS, fill=True)  # row 8, cols 5..6
    blended = check_transition_blends(
        stepped_doc, {"gd_n": stepped}, resolve_palette(stepped_doc.palette)
    )[0]
    assert blended.is_hard is False
    assert blended.blend_width == 3


def test_check_transition_blends_corner_policy() -> None:
    square_doc = _terrain_doc(
        [{"from_terrain": "grass", "to_terrain": "dirt", "tile_id": "gd_n", "mask": "NE"}]
    )
    square = check_transition_blends(
        square_doc, {"gd_n": _square_corner_tile()}, resolve_palette(square_doc.palette)
    )[0]
    assert square.is_hard is True  # a square L-corner boundary never wanders
    assert square.blend_width == 0

    stepped_doc = _terrain_doc(
        [{"from_terrain": "grass", "to_terrain": "dirt", "tile_id": "gd_n", "mask": "NE"}]
    )
    stepped = check_transition_blends(
        stepped_doc, {"gd_n": _stepped_corner_tile()}, resolve_palette(stepped_doc.palette)
    )[0]
    assert stepped.is_hard is False
    assert stepped.blend_width == 2


def test_check_transition_blends_skips_unrendered_tiles() -> None:
    doc = _terrain_doc(
        [
            {"from_terrain": "grass", "to_terrain": "dirt", "tile_id": "gd_n", "mask": "N"},
            {"from_terrain": "grass", "to_terrain": "dirt", "tile_id": "missing", "mask": "S"},
        ]
    )
    reports = check_transition_blends(
        doc, {"gd_n": _blended_north_tile()}, resolve_palette(doc.palette)
    )
    assert len(reports) == 1
    assert reports[0].tile_id == "gd_n"


def test_check_transition_blends_water_grass_stepped_patch_not_hard() -> None:
    """The round-3 water<->grass transitions: a water tile with a stepped grass
    patch (organic fringe + a couple of grass_detail weeds) must read as
    blended, not hard — the same geometry the demo's new gw_* tiles use."""
    WATER: RGBA = (42, 111, 151, 255)
    doc = _terrain_doc(
        [{"from_terrain": "grass", "to_terrain": "water", "tile_id": "gw_n", "mask": "N"}]
    )
    tile = _ringed()
    tile.draw_rect((1, 1), (14, 14), WATER, fill=True)
    tile.draw_rect((1, 1), (14, 4), GRASS, fill=True)  # rows 1..4 grass band
    tile.draw_rect((1, 5), (11, 1), GRASS, fill=True)  # row 5, cols 1..11
    tile.draw_rect((3, 6), (7, 1), GRASS, fill=True)  # row 6, cols 3..9
    tile.draw_rect((5, 7), (2, 1), GRASS, fill=True)  # row 7, cols 5..6
    tile.set_pixel(7, 8, GRASS)  # a weed poking into the water
    tile.set_pixel(11, 7, GRASS)
    report = check_transition_blends(doc, {"gw_n": tile}, resolve_palette(doc.palette))[0]
    assert report.is_hard is False
    assert report.blend_width >= 2
    assert report.materials >= 2


def test_check_transition_blends_dirt_water_stepped_patch_not_hard() -> None:
    """The round-4 dirt<->water transitions: a water tile with a stepped dirt
    patch must read as blended, not hard — the demo's dw_* shoreline tiles."""
    WATER: RGBA = (42, 111, 151, 255)
    doc = _terrain_doc(
        [{"from_terrain": "dirt", "to_terrain": "water", "tile_id": "dw_n", "mask": "N"}]
    )
    tile = _ringed()
    tile.draw_rect((1, 1), (14, 14), WATER, fill=True)
    tile.draw_rect((1, 1), (14, 4), DIRT, fill=True)  # rows 1..4 dirt band
    tile.draw_rect((1, 5), (11, 1), DIRT, fill=True)  # row 5, cols 1..11
    tile.draw_rect((3, 6), (7, 1), DIRT, fill=True)  # row 6, cols 3..9
    tile.draw_rect((5, 7), (2, 1), DIRT, fill=True)  # row 7, cols 5..6
    tile.set_pixel(7, 8, DIRT)  # a pebble poking into the water
    report = check_transition_blends(doc, {"dw_n": tile}, resolve_palette(doc.palette))[0]
    assert report.is_hard is False
    assert report.blend_width >= 2
    assert report.materials >= 2


def test_check_transition_blends_flags_single_material_tile() -> None:
    doc = _terrain_doc(
        [{"from_terrain": "grass", "to_terrain": "dirt", "tile_id": "gd_n", "mask": "N"}]
    )
    reports = check_transition_blends(
        doc, {"gd_n": _single_material_tile()}, resolve_palette(doc.palette)
    )
    assert len(reports) == 1
    assert reports[0].is_hard is True
    assert reports[0].materials == 1
    assert "single material" in reports[0].note
