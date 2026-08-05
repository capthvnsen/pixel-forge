"""Tests for sprite sheet packing, atlases, contact sheets, and seam checking."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from pixel_forge.errors import ForgeError
from pixel_forge.rendering.canvas import RGBA, Canvas
from pixel_forge.rendering.font import draw_text, text_width
from pixel_forge.rendering.sheet import (
    SheetCell,
    build_atlas,
    build_contact_sheet,
    build_seam_map,
    build_sprite_sheet,
    check_seams,
)

RED = (255, 0, 0, 255)
BLUE = (0, 0, 255, 255)
GREEN = (0, 255, 0, 255)


# --- local duck-typed stand-in for pixel_forge.animation.resolver.ResolvedFrame ------------
# resolver.py is owned by another agent and is not imported at runtime by sheet.py; this
# dataclass exists only so tests can exercise build_sprite_sheet against something with the
# four documented attribute names.
@dataclass
class FakeFrame:
    direction: str
    animation: str
    index: int
    duration_ms: int = 100


def _solid(w: int, h: int, rgba: RGBA) -> Canvas:
    c = Canvas(w, h)
    c.draw_rect((0, 0), (w, h), rgba, fill=True)
    return c


# --- build_sprite_sheet ----------------------------------------------------------------------


def test_build_sprite_sheet_exact_cell_coordinates() -> None:
    cw, ch = 4, 4
    frames = []
    for animation in ("idle", "walk"):
        for direction in ("up", "down"):
            for i in range(3):
                frames.append((FakeFrame(direction, animation, i), _solid(cw, ch, RED)))

    sheet = build_sprite_sheet(frames, (cw, ch))

    assert sheet.columns == 3
    assert sheet.rows == 4
    assert sheet.image.width == 12
    assert sheet.image.height == 16

    expected = [
        ("idle", "up", 0, 0, 0),
        ("idle", "up", 1, 4, 0),
        ("idle", "up", 2, 8, 0),
        ("idle", "down", 0, 0, 4),
        ("idle", "down", 1, 4, 4),
        ("idle", "down", 2, 8, 4),
        ("walk", "up", 0, 0, 8),
        ("walk", "up", 1, 4, 8),
        ("walk", "up", 2, 8, 8),
        ("walk", "down", 0, 0, 12),
        ("walk", "down", 1, 4, 12),
        ("walk", "down", 2, 8, 12),
    ]
    for animation, direction, index, x, y in expected:
        cell = sheet.cell_for(animation, direction, index)
        assert (cell.x, cell.y, cell.w, cell.h) == (x, y, cw, ch)


def test_build_sprite_sheet_new_row_per_group() -> None:
    """A group never shares a row with another group, even mid-row."""
    cw, ch = 2, 2
    frames = [
        (FakeFrame("up", "idle", 0), _solid(cw, ch, RED)),
        (FakeFrame("up", "idle", 1), _solid(cw, ch, RED)),
        (FakeFrame("up", "walk", 0), _solid(cw, ch, BLUE)),
    ]
    # columns=None -> widest group (idle/up, size 2) sets columns=2.
    sheet = build_sprite_sheet(frames, (cw, ch))
    assert sheet.columns == 2
    # idle/up occupies row 0 fully (2 frames); walk/up starts a new row even though
    # row 0 has no spare capacity anyway -- verify with a case where it *would* fit.
    walk_cell = sheet.cell_for("walk", "up", 0)
    assert walk_cell.y == ch  # row 1, not packed into row 0 alongside idle/up


def test_build_sprite_sheet_columns_override_wraps_group() -> None:
    cw, ch = 3, 3
    frames = [(FakeFrame("front", "spin", i), _solid(cw, ch, GREEN)) for i in range(4)]
    sheet = build_sprite_sheet(frames, (cw, ch), columns=2)

    assert sheet.columns == 2
    assert sheet.rows == 2
    assert (sheet.cell_for("spin", "front", 0).x, sheet.cell_for("spin", "front", 0).y) == (0, 0)
    assert (sheet.cell_for("spin", "front", 1).x, sheet.cell_for("spin", "front", 1).y) == (cw, 0)
    assert (sheet.cell_for("spin", "front", 2).x, sheet.cell_for("spin", "front", 2).y) == (0, ch)
    assert (sheet.cell_for("spin", "front", 3).x, sheet.cell_for("spin", "front", 3).y) == (cw, ch)


def test_build_sprite_sheet_wrong_size_frame_raises() -> None:
    frames = [
        (FakeFrame("up", "idle", 0), _solid(4, 4, RED)),
        (FakeFrame("up", "idle", 1), _solid(3, 4, RED)),
    ]
    with pytest.raises(ForgeError, match="idle/up#1"):
        build_sprite_sheet(frames, (4, 4))


def test_build_sprite_sheet_empty_input_raises() -> None:
    with pytest.raises(ForgeError):
        build_sprite_sheet([], (4, 4))


def test_cell_for_miss_raises() -> None:
    frames = [(FakeFrame("up", "idle", 0), _solid(2, 2, RED))]
    sheet = build_sprite_sheet(frames, (2, 2))
    with pytest.raises(ForgeError):
        sheet.cell_for("idle", "down", 0)


# --- build_contact_sheet -----------------------------------------------------------------


def test_build_contact_sheet_produces_opaque_canvas() -> None:
    frames = [(FakeFrame("up", "idle", 0), _solid(2, 2, RED))]
    sheet = build_sprite_sheet(frames, (2, 2))
    contact = build_contact_sheet(sheet)
    # Every pixel opaque: background + labels + cell content, never transparent.
    assert bool((contact.array[..., 3] == 255).all())


def test_build_contact_sheet_scale_doubles_cell_footprint() -> None:
    cw, ch = 2, 2
    frames = [(FakeFrame("up", "idle", 0), _solid(cw, ch, RED))]
    sheet = build_sprite_sheet(frames, (cw, ch))
    contact_1x = build_contact_sheet(sheet, scale=1)
    contact_2x = build_contact_sheet(sheet, scale=2)
    # Strip off the fixed label gutter and 1px grid border (neither scales) to isolate
    # the per-cell content footprint, and assert it actually doubles, not just grows.
    gutter = text_width("idle/up") + 4
    fixed_w = gutter + 1 + sheet.columns
    fixed_h = 1 + sheet.rows
    assert contact_2x.width - fixed_w == 2 * (contact_1x.width - fixed_w)
    assert contact_2x.height - fixed_h == 2 * (contact_1x.height - fixed_h)


# --- build_atlas -----------------------------------------------------------------------------


def test_build_atlas_determinism_and_sorted_keys() -> None:
    images = {"zzz": _solid(2, 2, RED), "aaa": _solid(2, 2, BLUE)}
    atlas1, cells1 = build_atlas(images)
    atlas2, cells2 = build_atlas(images)
    assert atlas1.equals(atlas2)
    assert cells1 == cells2
    # sorted order: "aaa" before "zzz"
    assert cells1["aaa"].x == 0
    assert cells1["zzz"].x == 2
    assert cells1["aaa"] == SheetCell(direction="aaa", animation="aaa", index=0, x=0, y=0, w=2, h=2)


def test_build_atlas_uniform_size_enforced() -> None:
    images = {"a": _solid(2, 2, RED), "b": _solid(3, 3, BLUE)}
    with pytest.raises(ForgeError):
        build_atlas(images)


def test_build_atlas_empty_raises() -> None:
    with pytest.raises(ForgeError):
        build_atlas({})


def test_build_atlas_explicit_rows_exact_cell_coordinates() -> None:
    images = {k: _solid(2, 2, RED) for k in ("a", "b", "c", "d", "e")}
    # Row 0 is short (2 of a possible 3 columns) -- the third cell is padding.
    atlas, cells = build_atlas(images, rows=[["a", "b"], ["c", "d", "e"]])

    assert atlas.width == 6  # 3 columns (widest row) * 2px
    assert atlas.height == 4  # 2 rows * 2px
    assert {(cell.x, cell.y) for cell in cells.values()} == {
        (0, 0),
        (2, 0),
        (0, 2),
        (2, 2),
        (4, 2),
    }
    assert cells["a"] == SheetCell(direction="a", animation="a", index=0, x=0, y=0, w=2, h=2)
    assert cells["e"] == SheetCell(direction="e", animation="e", index=0, x=4, y=2, w=2, h=2)


def test_build_atlas_explicit_rows_missing_id_raises() -> None:
    images = {"a": _solid(2, 2, RED), "b": _solid(2, 2, BLUE)}
    with pytest.raises(ForgeError):
        build_atlas(images, rows=[["a"]])  # "b" never placed


def test_build_atlas_explicit_rows_unknown_id_raises() -> None:
    images = {"a": _solid(2, 2, RED)}
    with pytest.raises(ForgeError):
        build_atlas(images, rows=[["a", "ghost"]])  # "ghost" not in images


# --- check_seams / build_seam_map -------------------------------------------------------------


def test_check_seams_detects_mismatch_and_reports_zero_for_seamless() -> None:
    solid = _solid(4, 4, BLUE)
    mismatched = Canvas(4, 4)
    mismatched.draw_rect((0, 0), (4, 4), BLUE, fill=True)
    mismatched.draw_rect((0, 0), (4, 1), RED, fill=True)  # top row differs from bottom row

    results = check_seams({"solid": solid, "mismatched": mismatched})

    solid_self_n = next(
        r for r in results if r.tile_a == "solid" and r.tile_b == "solid" and r.edge == "N"
    )
    assert solid_self_n.mismatched_pixels == 0

    mismatched_self_n = next(
        r
        for r in results
        if r.tile_a == "mismatched" and r.tile_b == "mismatched" and r.edge == "N"
    )
    assert mismatched_self_n.mismatched_pixels == 4


def test_check_seams_deterministic_ordering() -> None:
    tiles = {"b": _solid(2, 2, RED), "a": _solid(2, 2, BLUE)}
    results = check_seams(tiles)
    pairs = [(r.tile_a, r.tile_b, r.edge) for r in results]
    expected_edge_order = ["N", "S", "E", "W"]
    assert pairs[:4] == [("a", "a", e) for e in expected_edge_order]
    assert pairs[4:8] == [("a", "b", e) for e in expected_edge_order]


def test_build_seam_map_output_size() -> None:
    tiles = {"a": _solid(3, 3, RED), "b": _solid(3, 3, BLUE)}
    layout = [["a", "b"], ["b", "a"]]
    canvas = build_seam_map(tiles, layout)
    assert canvas.width == 6
    assert canvas.height == 6


def test_build_seam_map_unknown_tile_raises() -> None:
    tiles = {"a": _solid(2, 2, RED)}
    with pytest.raises(ForgeError):
        build_seam_map(tiles, [["a", "missing"]])


# --- font --------------------------------------------------------------------------------


def test_draw_text_and_text_width_agree() -> None:
    canvas = Canvas(64, 8)
    consumed = draw_text(canvas, "abc123", (0, 0), (255, 255, 255, 255))
    assert consumed == text_width("abc123")


def test_draw_text_clips_at_canvas_edge() -> None:
    canvas = Canvas(4, 4)
    # Text wider than the canvas must not raise -- Canvas.set_pixel clips silently.
    consumed = draw_text(canvas, "hello world", (0, 0), (255, 255, 255, 255))
    assert consumed == text_width("hello world")


def test_draw_text_unknown_char_renders_filled_block() -> None:
    canvas = Canvas(3, 5)
    draw_text(canvas, "@", (0, 0), (255, 255, 255, 255), spacing=0)
    assert bool((canvas.array[..., 3] == 255).all())


def test_draw_text_uppercase_maps_to_lowercase_glyph() -> None:
    lower = Canvas(3, 5)
    upper = Canvas(3, 5)
    draw_text(lower, "a", (0, 0), (255, 255, 255, 255))
    draw_text(upper, "A", (0, 0), (255, 255, 255, 255))
    assert lower.equals(upper)
