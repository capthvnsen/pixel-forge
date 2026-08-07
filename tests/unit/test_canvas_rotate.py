"""`Canvas.rotate`: joint-pivot nearest-neighbour rotation, pure integer math.

Conventions under test (see `Canvas.rotate`'s docstring):
- pixels are lattice points at integer coordinates; the pixel at the pivot maps to
  itself at every angle;
- positive angles rotate clockwise in screen coordinates (y down);
- inverse mapping with round-to-nearest, halves away from zero;
- multiples of 90 use exact integer coefficients; multiples of 360 are identity.
"""

from __future__ import annotations

import pytest

from pixel_forge.rendering.canvas import RGBA, Canvas

RED: RGBA = (255, 0, 0, 255)
GREEN: RGBA = (0, 255, 0, 255)
BLUE: RGBA = (0, 0, 255, 255)
TRANSPARENT: RGBA = (0, 0, 0, 0)


def _canvas_5x5() -> Canvas:
    """5x5 canvas, pivot at (2, 2): red east of pivot, green at the pivot."""
    c = Canvas(5, 5)
    c.set_pixel(3, 2, RED)  # dx=+1, dy=0 from pivot
    c.set_pixel(2, 2, GREEN)
    return c


# ---- identity ------------------------------------------------------------


def test_rotate_zero_degrees_is_identity() -> None:
    c = _canvas_5x5()
    assert c.rotate((2, 2), 0.0).equals(c)


@pytest.mark.parametrize("angle", [360.0, -360.0, 720.0])
def test_rotate_full_turns_are_identity(angle: float) -> None:
    c = _canvas_5x5()
    assert c.rotate((2, 2), angle).equals(c)


def test_rotate_identity_returns_a_copy() -> None:
    c = _canvas_5x5()
    rotated = c.rotate((2, 2), 0.0)
    rotated.set_pixel(0, 0, BLUE)
    assert c.get_pixel(0, 0) == TRANSPARENT


# ---- exact quarter turns --------------------------------------------------


def test_rotate_90_clockwise() -> None:
    # 90 deg clockwise maps (dx, dy) -> (-dy, dx): east of pivot goes south.
    rotated = _canvas_5x5().rotate((2, 2), 90.0)
    assert rotated.get_pixel(2, 3) == RED  # (3, 2) -> (2, 3)
    assert rotated.get_pixel(3, 2) == TRANSPARENT
    assert rotated.get_pixel(2, 2) == GREEN  # pivot pixel stays


def test_rotate_180() -> None:
    rotated = _canvas_5x5().rotate((2, 2), 180.0)
    assert rotated.get_pixel(1, 2) == RED  # (3, 2) -> (1, 2)
    assert rotated.get_pixel(3, 2) == TRANSPARENT
    assert rotated.get_pixel(2, 2) == GREEN


def test_rotate_270_clockwise() -> None:
    # 270 deg clockwise = 90 deg counter-clockwise: east of pivot goes north.
    rotated = _canvas_5x5().rotate((2, 2), 270.0)
    assert rotated.get_pixel(2, 1) == RED  # (3, 2) -> (2, 1)
    assert rotated.get_pixel(3, 2) == TRANSPARENT
    assert rotated.get_pixel(2, 2) == GREEN


def test_rotate_quarter_turns_compose_to_identity() -> None:
    c = _canvas_5x5()
    assert c.rotate((2, 2), 90.0).rotate((2, 2), 90.0).equals(c.rotate((2, 2), 180.0))
    four_turns = c
    for _ in range(4):
        four_turns = four_turns.rotate((2, 2), 90.0)
    assert four_turns.equals(c)


def test_rotate_negative_90_equals_270() -> None:
    c = _canvas_5x5()
    assert c.rotate((2, 2), -90.0).equals(c.rotate((2, 2), 270.0))


# ---- pivot invariance ------------------------------------------------------


@pytest.mark.parametrize("angle", [1.0, 30.0, 45.0, 90.0, 137.5, 200.0, 359.0])
def test_pivot_pixel_stays_put(angle: float) -> None:
    # The pivot is a lattice point, so its offset is (0, 0) and it maps to itself
    # exactly at every angle — no rounding rule is involved.
    rotated = _canvas_5x5().rotate((2, 2), angle)
    assert rotated.get_pixel(2, 2) == GREEN


def test_rotate_about_noncentral_pivot() -> None:
    c = Canvas(5, 5)
    c.set_pixel(1, 0, RED)  # dx=+1, dy=0 from pivot (0, 0)
    rotated = c.rotate((0, 0), 90.0)
    assert rotated.get_pixel(0, 1) == RED  # east -> south about the corner pivot


# ---- non-quarter angles: the fixed-point rounding rule ---------------------


def test_rotate_45_uses_fixed_point_nearest() -> None:
    # cos45 = sin45 -> fixed-point 11585 / 16384. A destination pixel 2px east of
    # the pivot inverse-maps to source offset rdiv(2*11585) = rdiv(1.414..) = 1
    # east, rdiv(-2*11585) = -1 north, i.e. src (3, 1) for dst (4, 2).
    c = Canvas(5, 5)
    c.set_pixel(3, 1, BLUE)
    rotated = c.rotate((2, 2), 45.0)
    assert rotated.get_pixel(4, 2) == BLUE


def test_rotate_45_symmetric_about_pivot() -> None:
    # Opposite destination offsets pull from opposite source offsets.
    c = Canvas(5, 5)
    c.set_pixel(3, 1, BLUE)  # maps to dst (4, 2), see above
    c.set_pixel(1, 3, RED)  # opposite offset, maps to dst (0, 2)
    rotated = c.rotate((2, 2), 45.0)
    assert rotated.get_pixel(4, 2) == BLUE
    assert rotated.get_pixel(0, 2) == RED


# ---- determinism / colour discipline ---------------------------------------


def test_rotate_is_deterministic_across_calls() -> None:
    c = Canvas(16, 16)
    c.draw_ellipse((2, 3), (9, 7), RED, fill=True)
    c.draw_line((0, 0), (15, 11), GREEN)
    first = c.rotate((8, 8), 33.3)
    for _ in range(5):
        assert c.rotate((8, 8), 33.3).equals(first)


def test_rotate_preserves_binary_alpha_and_colours() -> None:
    c = Canvas(12, 12)
    c.draw_rect((3, 3), (4, 5), RED, fill=True)
    c.set_pixel(0, 0, GREEN)
    rotated = c.rotate((6, 6), 45.0)
    alphas = {int(a) for a in rotated.array[..., 3].flat}
    assert alphas <= {0, 255}
    assert rotated.colors() <= c.colors()  # no interpolation, no new colours


def test_rotate_clips_out_of_bounds_and_vacates() -> None:
    c = Canvas(4, 4)
    c.set_pixel(0, 3, RED)  # 90 deg CW about (0, 0): south of pivot goes west, off-canvas
    rotated = c.rotate((0, 0), 90.0)
    assert rotated.opaque_count() == 0  # clipped, not wrapped
    assert rotated.colors() == set()


def test_rotate_does_not_mutate_source() -> None:
    c = _canvas_5x5()
    snapshot = c.copy()
    c.rotate((2, 2), 45.0)
    assert c.equals(snapshot)
