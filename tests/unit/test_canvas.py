"""Exact-pixel tests for the deterministic raster core."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from pixel_forge.errors import RenderError
from pixel_forge.rendering.canvas import Canvas
from pixel_forge.rendering.shapes import draw_shape, shape_bounds

RED = (255, 0, 0, 255)
BLUE = (0, 0, 255, 255)
GREEN = (0, 255, 0, 255)


# --- local duck-typed stand-in for pixel_forge.schemas.common.Shape ------------------------
# schemas.common is owned by another agent and is not imported at runtime by shapes.py; this
# dataclass exists only so tests can exercise draw_shape/shape_bounds against something with
# the documented attribute names.
@dataclass
class FakeShape:
    op: str
    color: str = "ink"
    at: tuple[int, int] = (0, 0)
    size: tuple[int, int] = (0, 0)
    fill: bool = True
    start: tuple[int, int] = (0, 0)
    end: tuple[int, int] = (0, 0)


def _rows(c: Canvas) -> list[list[tuple[int, int, int, int]]]:
    return [[c.get_pixel(x, y) for x in range(c.width)] for y in range(c.height)]


# --- construction / basic pixel ops ---------------------------------------------------------


def test_new_canvas_is_fully_transparent() -> None:
    c = Canvas(3, 2)
    assert np.array_equal(c.array, np.zeros((2, 3, 4), dtype=np.uint8))


def test_negative_or_zero_dims_raise() -> None:
    with pytest.raises(ValueError):
        Canvas(0, 5)
    with pytest.raises(ValueError):
        Canvas(5, -1)


def test_set_pixel_invalid_alpha_raises() -> None:
    c = Canvas(2, 2)
    with pytest.raises(ValueError):
        c.set_pixel(0, 0, (1, 2, 3, 128))


def test_set_pixel_out_of_bounds_is_noop() -> None:
    c = Canvas(2, 2)
    c.set_pixel(10, 10, RED)
    c.set_pixel(-1, 0, RED)
    assert c.array.sum() == 0


def test_get_set_pixel_roundtrip() -> None:
    c = Canvas(2, 2)
    c.set_pixel(1, 0, BLUE)
    assert c.get_pixel(1, 0) == BLUE
    assert c.get_pixel(0, 0) == (0, 0, 0, 0)


# --- lines ------------------------------------------------------------------------------------


def test_draw_line_diagonal_exact_pixels() -> None:
    c = Canvas(5, 5)
    c.draw_line((0, 0), (3, 3), RED)
    expected = {(0, 0), (1, 1), (2, 2), (3, 3)}
    hit = {(x, y) for y in range(5) for x in range(5) if c.get_pixel(x, y)[3] == 255}
    assert hit == expected


def test_draw_line_shallow_exact_pixels() -> None:
    c = Canvas(6, 3)
    c.draw_line((0, 0), (4, 1), RED)
    expected = [(0, 0), (1, 0), (2, 1), (3, 1), (4, 1)]
    hit = [(x, y) for y in range(3) for x in range(6) if c.get_pixel(x, y)[3] == 255]
    assert hit == expected


# --- rects ------------------------------------------------------------------------------------


def test_draw_rect_filled_exact_array() -> None:
    c = Canvas(4, 4)
    c.draw_rect((1, 1), (2, 2), RED, fill=True)
    alpha = c.array[..., 3]
    expected = np.array(
        [
            [0, 0, 0, 0],
            [0, 255, 255, 0],
            [0, 255, 255, 0],
            [0, 0, 0, 0],
        ],
        dtype=np.uint8,
    )
    assert np.array_equal(alpha, expected)


def test_draw_rect_outline_exact_array() -> None:
    c = Canvas(5, 4)
    c.draw_rect((0, 0), (5, 4), RED, fill=False)
    alpha = c.array[..., 3]
    expected = np.array(
        [
            [255, 255, 255, 255, 255],
            [255, 0, 0, 0, 255],
            [255, 0, 0, 0, 255],
            [255, 255, 255, 255, 255],
        ],
        dtype=np.uint8,
    )
    assert np.array_equal(alpha, expected)


def test_draw_rect_zero_or_negative_size_draws_nothing() -> None:
    c = Canvas(3, 3)
    c.draw_rect((0, 0), (0, 5), RED, fill=True)
    c.draw_rect((0, 0), (5, -1), RED, fill=False)
    assert c.array.sum() == 0


# --- ellipses ---------------------------------------------------------------------------------


def test_draw_ellipse_7x7_symmetry_and_shape() -> None:
    c = Canvas(7, 7)
    c.draw_ellipse((0, 0), (7, 7), RED, fill=True)
    mask = c.array[..., 3] == 255
    expected = np.array(
        [
            [0, 0, 1, 1, 1, 0, 0],
            [0, 1, 1, 1, 1, 1, 0],
            [1, 1, 1, 1, 1, 1, 1],
            [1, 1, 1, 1, 1, 1, 1],
            [1, 1, 1, 1, 1, 1, 1],
            [0, 1, 1, 1, 1, 1, 0],
            [0, 0, 1, 1, 1, 0, 0],
        ],
        dtype=bool,
    )
    assert np.array_equal(mask, expected)
    assert np.array_equal(mask, np.fliplr(mask))
    assert np.array_equal(mask, np.flipud(mask))


def test_draw_ellipse_8x8_symmetry_and_shape() -> None:
    c = Canvas(8, 8)
    c.draw_ellipse((0, 0), (8, 8), RED, fill=True)
    mask = c.array[..., 3] == 255
    expected = np.array(
        [
            [0, 0, 1, 1, 1, 1, 0, 0],
            [0, 1, 1, 1, 1, 1, 1, 0],
            [1, 1, 1, 1, 1, 1, 1, 1],
            [1, 1, 1, 1, 1, 1, 1, 1],
            [1, 1, 1, 1, 1, 1, 1, 1],
            [1, 1, 1, 1, 1, 1, 1, 1],
            [0, 1, 1, 1, 1, 1, 1, 0],
            [0, 0, 1, 1, 1, 1, 0, 0],
        ],
        dtype=bool,
    )
    assert np.array_equal(mask, expected)
    assert np.array_equal(mask, np.fliplr(mask))
    assert np.array_equal(mask, np.flipud(mask))


def test_draw_ellipse_outline_7x7() -> None:
    c = Canvas(7, 7)
    c.draw_ellipse((0, 0), (7, 7), RED, fill=False)
    mask = c.array[..., 3] == 255
    expected = np.array(
        [
            [0, 0, 1, 1, 1, 0, 0],
            [0, 1, 0, 0, 0, 1, 0],
            [1, 0, 0, 0, 0, 0, 1],
            [1, 0, 0, 0, 0, 0, 1],
            [1, 0, 0, 0, 0, 0, 1],
            [0, 1, 0, 0, 0, 1, 0],
            [0, 0, 1, 1, 1, 0, 0],
        ],
        dtype=bool,
    )
    assert np.array_equal(mask, expected)


# --- blit / mirror / translate ----------------------------------------------------------------


def test_blit_binary_alpha_does_not_erase_with_transparent_source() -> None:
    dst = Canvas(4, 4)
    dst.draw_rect((0, 0), (4, 4), (10, 20, 30, 255), fill=True)
    src = Canvas(3, 3)
    src.set_pixel(0, 0, RED)
    src.set_pixel(1, 1, (0, 255, 0, 0))  # transparent: must not erase destination
    src.set_pixel(2, 2, BLUE)
    dst.blit(src, (1, 1))
    expected = [
        [(10, 20, 30, 255)] * 4,
        [(10, 20, 30, 255), RED, (10, 20, 30, 255), (10, 20, 30, 255)],
        [(10, 20, 30, 255)] * 4,
        [(10, 20, 30, 255), (10, 20, 30, 255), (10, 20, 30, 255), BLUE],
    ]
    assert _rows(dst) == expected


def test_blit_clips_at_edges() -> None:
    dst = Canvas(2, 2)
    src = Canvas(2, 2)
    src.draw_rect((0, 0), (2, 2), RED, fill=True)
    dst.blit(src, (1, 1))
    assert dst.get_pixel(1, 1) == RED
    assert dst.get_pixel(0, 0) == (0, 0, 0, 0)


def test_mirror_x_is_involution() -> None:
    c = Canvas(3, 2)
    c.set_pixel(0, 0, RED)
    c.set_pixel(2, 1, BLUE)
    twice = c.mirror_x().mirror_x()
    assert c.equals(twice)
    once = c.mirror_x()
    assert once.get_pixel(2, 0) == RED
    assert once.get_pixel(0, 1) == BLUE


def test_translate_shifts_and_discards_overflow() -> None:
    c = Canvas(3, 3)
    c.set_pixel(2, 2, RED)
    moved = c.translate((1, 1))
    assert moved.array.sum() == 0  # pushed to (3,3), off-canvas, discarded
    c2 = Canvas(3, 3)
    c2.set_pixel(0, 0, RED)
    moved2 = c2.translate((1, 1))
    assert moved2.get_pixel(1, 1) == RED
    assert moved2.get_pixel(0, 0) == (0, 0, 0, 0)


# --- replace_color / colors / bbox --------------------------------------------------------------


def test_replace_color_returns_new_canvas() -> None:
    c = Canvas(2, 1)
    c.set_pixel(0, 0, RED)
    replaced = c.replace_color(RED, BLUE)
    assert replaced.get_pixel(0, 0) == BLUE
    assert c.get_pixel(0, 0) == RED  # original untouched


def test_colors_and_bbox_on_empty_canvas() -> None:
    c = Canvas(3, 3)
    assert c.colors() == set()
    assert c.bbox() is None
    assert c.opaque_count() == 0


def test_colors_and_bbox_on_nonempty_canvas() -> None:
    c = Canvas(4, 4)
    c.set_pixel(1, 1, RED)
    c.set_pixel(2, 0, RED)
    c.set_pixel(3, 3, BLUE)
    assert c.colors() == {RED, BLUE}
    assert c.bbox() == (1, 0, 4, 4)
    assert c.opaque_count() == 3


# --- scale / copy / equals -----------------------------------------------------------------------


def test_scale_produces_exact_blocks() -> None:
    c = Canvas(2, 2)
    c.set_pixel(0, 0, RED)
    c.set_pixel(1, 1, BLUE)
    scaled = c.scale(3)
    assert scaled.width == 6
    assert scaled.height == 6
    for y in range(3):
        for x in range(3):
            assert scaled.get_pixel(x, y) == RED
    for y in range(3, 6):
        for x in range(3, 6):
            assert scaled.get_pixel(x, y) == BLUE
    for y in range(3):
        for x in range(3, 6):
            assert scaled.get_pixel(x, y) == (0, 0, 0, 0)


def test_scale_invalid_factor_raises() -> None:
    c = Canvas(1, 1)
    with pytest.raises(ValueError):
        c.scale(0)
    with pytest.raises(ValueError):
        c.scale(-2)


def test_copy_is_independent() -> None:
    c = Canvas(2, 2)
    c.set_pixel(0, 0, RED)
    dup = c.copy()
    dup.set_pixel(1, 1, BLUE)
    assert c.get_pixel(1, 1) == (0, 0, 0, 0)
    assert c.equals(dup) is False


# --- image round trip / save_png determinism ------------------------------------------------------


def test_to_image_from_image_roundtrip() -> None:
    c = Canvas(2, 2)
    c.set_pixel(0, 0, RED)
    img = c.to_image()
    assert img.mode == "RGBA"
    back = Canvas.from_image(img)
    assert c.equals(back)


def test_save_png_is_byte_deterministic(tmp_path: Path) -> None:
    c = Canvas(3, 3)
    c.draw_ellipse((0, 0), (3, 3), RED, fill=True)
    p1 = tmp_path / "a.png"
    p2 = tmp_path / "b.png"
    c.save_png(p1)
    c.save_png(p2)
    assert p1.read_bytes() == p2.read_bytes()


# --- draw_shape / shape_bounds -----------------------------------------------------------------


def test_draw_shape_pixel_with_origin() -> None:
    c = Canvas(5, 5)
    shape = FakeShape(op="pixel", at=(1, 1))
    draw_shape(c, shape, origin=(2, 3), rgba=RED)
    assert c.get_pixel(3, 4) == RED
    assert c.opaque_count() == 1
    assert shape_bounds(shape, origin=(2, 3)) == (3, 4, 4, 5)


def test_draw_shape_line_with_origin() -> None:
    c = Canvas(6, 6)
    shape = FakeShape(op="line", start=(0, 0), end=(2, 0))
    draw_shape(c, shape, origin=(1, 1), rgba=RED)
    assert [c.get_pixel(x, 1) for x in range(1, 4)] == [RED, RED, RED]
    assert shape_bounds(shape, origin=(1, 1)) == (1, 1, 4, 2)


def test_draw_shape_rect_with_origin() -> None:
    c = Canvas(6, 6)
    shape = FakeShape(op="rect", at=(0, 0), size=(2, 2), fill=True)
    draw_shape(c, shape, origin=(2, 2), rgba=RED)
    for y in range(2, 4):
        for x in range(2, 4):
            assert c.get_pixel(x, y) == RED
    assert shape_bounds(shape, origin=(2, 2)) == (2, 2, 4, 4)


def test_draw_shape_ellipse_with_origin() -> None:
    c = Canvas(8, 8)
    shape = FakeShape(op="ellipse", at=(0, 0), size=(4, 4), fill=True)
    draw_shape(c, shape, origin=(1, 1), rgba=RED)
    plain = Canvas(4, 4)
    plain.draw_ellipse((0, 0), (4, 4), RED, fill=True)
    shifted = Canvas(8, 8)
    shifted.blit(plain, (1, 1))
    assert c.equals(shifted)
    assert shape_bounds(shape, origin=(1, 1)) == (1, 1, 5, 5)


def test_draw_shape_unknown_op_raises() -> None:
    c = Canvas(2, 2)
    shape = FakeShape(op="spline")  # "polygon" used to be unknown; it is a real op now
    with pytest.raises(RenderError):
        draw_shape(c, shape, origin=(0, 0), rgba=RED)
    with pytest.raises(RenderError):
        shape_bounds(shape, origin=(0, 0))
