"""Exact-pixel and schema-validation tests for the `bitmap` shape op."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from pixel_forge.animation import resolve_frames
from pixel_forge.domain.palette import resolve_palette
from pixel_forge.errors import PaletteError, RenderError
from pixel_forge.rendering.canvas import Canvas
from pixel_forge.rendering.local import LocalRenderBackend
from pixel_forge.rendering.shapes import bitmap_size, draw_bitmap, draw_shape, shape_bounds
from pixel_forge.schemas import parse_asset_doc
from pixel_forge.schemas.common import BitmapShape

RED = (255, 0, 0, 255)
BLUE = (0, 0, 255, 255)
GREEN = (0, 255, 0, 255)
TRANS = (0, 0, 0, 0)

_PALETTE = {
    "id": "p",
    "colors": [
        {"id": "ink", "hex": "#000000"},
        {"id": "suit_mid", "hex": "#5050ff"},
        {"id": "suit_dark", "hex": "#101064"},
    ],
}


def _rows(c: Canvas) -> list[list[tuple[int, int, int, int]]]:
    return [[c.get_pixel(x, y) for x in range(c.width)] for y in range(c.height)]


def _doc(*, regions: dict[str, Any] | None = None, transforms: dict[str, Any] | None = None) -> Any:
    return parse_asset_doc(
        {
            "schema_version": 1,
            "asset": {"id": "x", "type": "character", "canvas": [8, 8]},
            "palette": _PALETTE,
            "directions": ["south"],
            "mirror": {},
            "anchors": {"root": [0, 0]},
            "regions": regions
            or {
                "body": {
                    "anchor": "root",
                    "layer": 0,
                    "shapes": [
                        {
                            "op": "bitmap",
                            "at": [0, 0],
                            "key": {"m": "suit_mid"},
                            "rows": ["mm", "mm"],
                        }
                    ],
                }
            },
            "direction_overrides": {},
            "animations": {
                "idle": {
                    "loop": True,
                    "frames": [{"duration_ms": 100, "events": [], "transforms": transforms or {}}],
                }
            },
            "export": {},
            "validation": {},
        }
    )


def _render_south(doc: Any) -> Canvas:
    palette = resolve_palette(doc.palette)
    frame = resolve_frames(doc)[0]
    return LocalRenderBackend().render_frame(doc, frame, palette)


# --- draw_bitmap: exact pixel arrays --------------------------------------------------------


def test_draw_bitmap_exact_pixel_array_with_transparency() -> None:
    shape = BitmapShape(
        op="bitmap",
        at=(0, 0),
        key={"o": "ink", "m": "suit_mid", "l": "suit_dark"},
        rows=["o.m", "l.o"],
    )
    colors = {"o": RED, "m": BLUE, "l": GREEN}
    c = Canvas(3, 2)
    draw_bitmap(c, shape, (0, 0), colors)
    assert _rows(c) == [
        [RED, TRANS, BLUE],
        [GREEN, TRANS, RED],
    ]


def test_origin_and_at_compose() -> None:
    shape = BitmapShape(op="bitmap", at=(2, 1), key={"x": "ink"}, rows=["x"])
    c = Canvas(6, 6)
    draw_bitmap(c, shape, (3, 2), {"x": RED})
    assert c.get_pixel(5, 3) == RED
    assert c.opaque_count() == 1


def test_transparent_chars_do_not_erase_underlying_layer() -> None:
    c = Canvas(3, 3)
    c.draw_rect((0, 0), (3, 3), RED, fill=True)
    shape = BitmapShape(op="bitmap", at=(0, 0), key={"b": "ink"}, rows=[".b.", "b.b", ".b."])
    draw_bitmap(c, shape, (0, 0), {"b": BLUE})
    assert _rows(c) == [
        [RED, BLUE, RED],
        [BLUE, RED, BLUE],
        [RED, BLUE, RED],
    ]


def test_clipping_at_all_four_edges_does_not_raise() -> None:
    c = Canvas(2, 2)
    top_left = BitmapShape(op="bitmap", at=(-1, -1), key={"x": "ink"}, rows=["xxx", "xxx", "xxx"])
    draw_bitmap(c, top_left, (0, 0), {"x": RED})
    bottom_right = BitmapShape(op="bitmap", at=(1, 1), key={"x": "ink"}, rows=["xxx", "xxx", "xxx"])
    draw_bitmap(c, bottom_right, (0, 0), {"x": RED})
    assert _rows(c) == [[RED, RED], [RED, RED]]


# --- bitmap_size / width / height / shape_bounds --------------------------------------------


def test_bitmap_size_and_properties() -> None:
    shape = BitmapShape(op="bitmap", at=(0, 0), key={"x": "ink"}, rows=["xxx", "xxx"])
    assert bitmap_size(shape) == (3, 2)
    assert shape.width == 3
    assert shape.height == 2


def test_shape_bounds_handles_bitmap() -> None:
    shape = BitmapShape(op="bitmap", at=(2, 3), key={"x": "ink"}, rows=["xxx", "xxx"])
    assert shape_bounds(shape, (0, 0)) == (2, 3, 5, 5)


def test_draw_shape_on_bitmap_raises_render_error() -> None:
    shape = BitmapShape(op="bitmap", at=(0, 0), key={"x": "ink"}, rows=["x"])
    c = Canvas(2, 2)
    with pytest.raises(RenderError, match="draw_bitmap"):
        draw_shape(c, shape, (0, 0), RED)


# --- schema validators -----------------------------------------------------------------------


def test_ragged_rows_rejected() -> None:
    with pytest.raises(ValidationError, match="ragged"):
        BitmapShape(op="bitmap", at=(0, 0), key={"x": "ink"}, rows=["xx", "x"])


def test_empty_rows_list_rejected() -> None:
    with pytest.raises(ValidationError, match="at least one row"):
        BitmapShape(op="bitmap", at=(0, 0), key={}, rows=[])


def test_empty_row_string_rejected() -> None:
    with pytest.raises(ValidationError, match="row 1 is empty"):
        BitmapShape(op="bitmap", at=(0, 0), key={"x": "ink"}, rows=["xx", ""])


def test_multi_char_key_rejected() -> None:
    with pytest.raises(ValidationError, match="exactly one character"):
        BitmapShape(op="bitmap", at=(0, 0), key={"ab": "ink"}, rows=["a"])


def test_dot_in_key_rejected() -> None:
    with pytest.raises(ValidationError, match="reserved for transparency"):
        BitmapShape(op="bitmap", at=(0, 0), key={".": "ink"}, rows=["a"])


def test_char_missing_from_key_rejected() -> None:
    with pytest.raises(ValidationError, match="not present in key"):
        BitmapShape(op="bitmap", at=(0, 0), key={"a": "ink"}, rows=["ab"])


def test_unused_key_entry_rejected() -> None:
    with pytest.raises(ValidationError, match="unused by any row"):
        BitmapShape(op="bitmap", at=(0, 0), key={"a": "ink", "z": "other"}, rows=["a"])


# --- compositor integration -------------------------------------------------------------------


def test_color_swap_recolors_bitmap() -> None:
    doc = _doc()
    palette = resolve_palette(doc.palette)
    base = _render_south(doc)
    assert base.get_pixel(0, 0) == palette.rgba("suit_mid")

    swapped_doc = _doc(transforms={"body": {"color_swap": {"suit_mid": "suit_dark"}}})
    swapped = _render_south(swapped_doc)
    assert swapped.get_pixel(0, 0) == palette.rgba("suit_dark")
    assert swapped.get_pixel(0, 0) != base.get_pixel(0, 0)


def test_color_swap_unknown_target_raises_palette_error() -> None:
    doc = _doc(transforms={"body": {"color_swap": {"suit_mid": "nonexistent"}}})
    with pytest.raises(PaletteError):
        _render_south(doc)


def test_scale_size_ignored_for_bitmap() -> None:
    base = _render_south(_doc())
    scaled = _render_south(_doc(transforms={"body": {"scale_size": [4, 4]}}))
    assert scaled.equals(base)


def test_region_mixes_rect_and_bitmap() -> None:
    doc = _doc(
        regions={
            "body": {
                "anchor": "root",
                "layer": 0,
                "shapes": [
                    {"op": "rect", "color": "ink", "at": [0, 0], "size": [4, 4]},
                    {
                        "op": "bitmap",
                        "at": [1, 1],
                        "key": {"m": "suit_mid"},
                        "rows": ["m"],
                    },
                ],
            }
        }
    )
    canvas = _render_south(doc)
    palette = resolve_palette(doc.palette)
    assert canvas.get_pixel(1, 1) == palette.rgba("suit_mid")
    assert canvas.get_pixel(0, 0) == palette.rgba("ink")


def test_bitmap_render_is_deterministic() -> None:
    doc = _doc()
    first = _render_south(doc)
    second = _render_south(doc)
    assert first.equals(second)
