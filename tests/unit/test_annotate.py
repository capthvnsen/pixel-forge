"""Tests for the diagnostic overlay views: annotate_frame, upscale_view,
build_annotated_contact."""

from __future__ import annotations

import numpy as np
import pytest

from pixel_forge.rendering.annotate import (
    OVERLAY_ANCHOR,
    OVERLAY_BASELINE,
    OVERLAY_BBOX,
    OVERLAY_GRID,
    annotate_frame,
    build_annotated_contact,
    upscale_view,
)
from pixel_forge.rendering.canvas import RGBA, Canvas
from pixel_forge.rendering.font import text_width
from pixel_forge.rendering.sheet import SheetCell, SpriteSheet

ART: RGBA = (10, 20, 30, 255)
ANCHORS = {"a": (8, 8)}


def _art_frame() -> Canvas:
    c = Canvas(16, 16)
    c.draw_rect((4, 4), (8, 8), ART, fill=True)
    return c


def _overlay_pixel_columns(canvas: Canvas, color: RGBA) -> set[tuple[int, int]]:
    return {
        (x, y)
        for x in range(canvas.width)
        for y in range(canvas.height)
        if canvas.get_pixel(x, y) == color
    }


# --- immutability -----------------------------------------------------------------------


def test_annotate_frame_does_not_mutate_input() -> None:
    frame = _art_frame()
    before = frame.array.copy()

    annotate_frame(frame, baseline_y=12, anchors=ANCHORS, bbox=True, grid=4, labels=True)

    assert np.array_equal(frame.array, before)


def test_upscale_view_does_not_mutate_input() -> None:
    frame = _art_frame()
    before = frame.array.copy()

    upscale_view(frame, 3)

    assert np.array_equal(frame.array, before)


def test_build_annotated_contact_does_not_mutate_sheet_image() -> None:
    cw, ch = 6, 6
    frame = Canvas(cw, ch)
    frame.draw_rect((1, 1), (3, 3), ART, fill=True)
    before = frame.array.copy()
    cell = SheetCell(direction="south", animation="idle", index=0, x=0, y=0, w=cw, h=ch)
    sheet = SpriteSheet(image=frame, cells=(cell,), columns=1, rows=1)

    build_annotated_contact(sheet, baseline_y=5, anchors={"a": (3, 3)}, scale=4)

    assert np.array_equal(sheet.image.array, before)


# --- baseline -----------------------------------------------------------------------------


def test_baseline_lands_on_exact_row_at_1x() -> None:
    frame = Canvas(10, 10)

    out = annotate_frame(frame, baseline_y=6, bbox=False)

    for x in range(10):
        assert out.get_pixel(x, 6) == OVERLAY_BASELINE
    assert out.get_pixel(0, 5) == (0, 0, 0, 0)
    assert out.get_pixel(0, 7) == (0, 0, 0, 0)


def test_baseline_lands_on_exact_row_at_scale_4() -> None:
    frame = Canvas(10, 10)
    upscaled = upscale_view(frame, 4)

    out = annotate_frame(upscaled, baseline_y=6 * 4, bbox=False)

    for x in range(upscaled.width):
        assert out.get_pixel(x, 24) == OVERLAY_BASELINE
    assert out.get_pixel(0, 23) == (0, 0, 0, 0)
    assert out.get_pixel(0, 25) == (0, 0, 0, 0)


def test_build_annotated_contact_baseline_is_crisp_not_smeared() -> None:
    """The upscale-then-annotate order must keep a 1px baseline 1px, never a scale-tall slab."""
    cw, ch = 8, 8
    frame = Canvas(cw, ch)
    frame.draw_rect((1, 1), (4, 4), ART, fill=True)
    cell = SheetCell(direction="south", animation="idle", index=0, x=0, y=0, w=cw, h=ch)
    sheet = SpriteSheet(image=frame, cells=(cell,), columns=1, rows=1)
    scale = 4

    contact = build_annotated_contact(sheet, baseline_y=6, scale=scale)

    run_len = cw * scale
    rows_with_full_run = []
    for y in range(contact.height):
        row = contact.array[y]
        is_overlay = np.all(row == np.array(OVERLAY_BASELINE, dtype=np.uint8), axis=-1)
        longest = current = 0
        for v in is_overlay.tolist():
            current = current + 1 if v else 0
            longest = max(longest, current)
        if longest >= run_len:
            rows_with_full_run.append(y)

    assert len(rows_with_full_run) == 1


# --- anchors --------------------------------------------------------------------------------


def test_anchor_crosshair_puts_five_pixels_in_expected_places() -> None:
    frame = Canvas(20, 20)

    out = annotate_frame(frame, anchors={"p": (10, 10)}, bbox=False, labels=False)

    expected = {(10, 10), (9, 10), (11, 10), (10, 9), (10, 11)}
    assert _overlay_pixel_columns(out, OVERLAY_ANCHOR) == expected


def test_anchor_at_canvas_edge_clips_without_raising() -> None:
    frame = Canvas(10, 10)

    out = annotate_frame(frame, anchors={"corner": (0, 0)}, bbox=False, labels=False)

    assert out.get_pixel(0, 0) == OVERLAY_ANCHOR
    assert out.get_pixel(1, 0) == OVERLAY_ANCHOR
    assert out.get_pixel(0, 1) == OVERLAY_ANCHOR
    # the off-canvas neighbours (-1, 0) and (0, -1) were silently dropped, not raised


def test_label_near_right_edge_is_clamped_inside_canvas() -> None:
    canvas_w, canvas_h = 30, 20
    name = "hi"
    ax, ay = 28, 10

    out = annotate_frame(Canvas(canvas_w, canvas_h), anchors={name: (ax, ay)}, bbox=False)

    tw = text_width(name)
    expected_x0 = max(0, min(ax + 3, canvas_w - tw))
    assert expected_x0 + tw <= canvas_w

    crosshair = {(ax, ay), (ax - 1, ay), (ax + 1, ay), (ax, ay - 1), (ax, ay + 1)}
    label_cols = {x for (x, y) in _overlay_pixel_columns(out, OVERLAY_ANCHOR) - crosshair}
    assert label_cols
    assert min(label_cols) == expected_x0
    assert max(label_cols) < canvas_w


# --- flags suppress only their own overlay ---------------------------------------------------


def test_bbox_false_suppresses_only_bbox() -> None:
    frame = _art_frame()
    full = annotate_frame(frame, baseline_y=12, anchors=ANCHORS, bbox=True, grid=4, labels=True)
    without = annotate_frame(frame, baseline_y=12, anchors=ANCHORS, bbox=False, grid=4, labels=True)

    assert OVERLAY_BBOX in full.colors()
    assert OVERLAY_BBOX not in without.colors()
    assert OVERLAY_BASELINE in without.colors()
    assert OVERLAY_GRID in without.colors()
    assert OVERLAY_ANCHOR in without.colors()


def test_grid_zero_suppresses_only_grid() -> None:
    frame = _art_frame()
    full = annotate_frame(frame, baseline_y=12, anchors=ANCHORS, bbox=True, grid=4, labels=True)
    without = annotate_frame(frame, baseline_y=12, anchors=ANCHORS, bbox=True, grid=0, labels=True)

    assert OVERLAY_GRID in full.colors()
    assert OVERLAY_GRID not in without.colors()
    assert OVERLAY_BBOX in without.colors()
    assert OVERLAY_BASELINE in without.colors()
    assert OVERLAY_ANCHOR in without.colors()


def test_labels_false_suppresses_only_labels() -> None:
    frame = _art_frame()

    with_labels = annotate_frame(frame, anchors=ANCHORS, bbox=True, grid=4, labels=True)
    without_labels = annotate_frame(frame, anchors=ANCHORS, bbox=True, grid=4, labels=False)

    count_with = len(_overlay_pixel_columns(with_labels, OVERLAY_ANCHOR))
    count_without = len(_overlay_pixel_columns(without_labels, OVERLAY_ANCHOR))

    assert count_without == 5  # crosshair only, no label glyphs
    assert count_with > count_without


# --- upscale_view -----------------------------------------------------------------------------


def test_upscale_view_produces_exact_3x3_blocks() -> None:
    frame = Canvas(2, 2)
    frame.set_pixel(0, 0, ART)
    frame.set_pixel(1, 1, (1, 2, 3, 255))

    out = upscale_view(frame, 3)

    assert (out.width, out.height) == (6, 6)
    for y in range(6):
        for x in range(6):
            assert out.get_pixel(x, y) == frame.get_pixel(x // 3, y // 3)


def test_upscale_view_scale_zero_raises() -> None:
    with pytest.raises(ValueError):
        upscale_view(Canvas(4, 4), 0)


# --- build_annotated_contact ------------------------------------------------------------------


def test_build_annotated_contact_size_and_determinism() -> None:
    cw, ch = 6, 6
    frame = Canvas(cw, ch)
    frame.draw_rect((1, 1), (3, 3), ART, fill=True)
    cell = SheetCell(direction="south", animation="idle", index=0, x=0, y=0, w=cw, h=ch)
    sheet = SpriteSheet(image=frame, cells=(cell,), columns=1, rows=1)
    scale = 4

    contact_a = build_annotated_contact(sheet, baseline_y=5, anchors={"a": (3, 3)}, scale=scale)
    contact_b = build_annotated_contact(sheet, baseline_y=5, anchors={"a": (3, 3)}, scale=scale)

    assert contact_a.equals(contact_b)

    label = f"{cell.animation}/{cell.direction}"
    gutter = text_width(label) + 4
    expected_w = gutter + 1 + sheet.columns * (cw * scale + 1)
    expected_h = 1 + sheet.rows * (ch * scale + 1)
    assert (contact_a.width, contact_a.height) == (expected_w, expected_h)


# --- overlay colours are outside any plausible sprite palette ------------------------------


def test_overlay_colors_are_not_in_source_frame_colors() -> None:
    frame = _art_frame()

    source_colors = frame.colors()
    overlays = {OVERLAY_BASELINE, OVERLAY_ANCHOR, OVERLAY_BBOX, OVERLAY_GRID}

    assert overlays.isdisjoint(source_colors)
