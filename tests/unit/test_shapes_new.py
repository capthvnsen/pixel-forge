"""Exact-pixel tests for the new shape DSL ops: polygon, arc, curve, bezier."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from pixel_forge.animation import resolve_frames
from pixel_forge.domain.palette import resolve_palette
from pixel_forge.rendering.canvas import Canvas
from pixel_forge.rendering.local import LocalRenderBackend
from pixel_forge.rendering.shapes import draw_shape, shape_bounds
from pixel_forge.schemas import parse_asset_doc
from pixel_forge.schemas.common import ArcShape, BezierShape, CurveShape, PolygonShape

RED = (255, 0, 0, 255)
BLUE = (0, 0, 255, 255)
GREEN = (0, 255, 0, 255)
TRANS = (0, 0, 0, 0)


def _opaque(c: Canvas) -> set[tuple[int, int]]:
    return {(x, y) for y in range(c.height) for x in range(c.width) if c.get_pixel(x, y)[3] == 255}


def _assert_binary_alpha(c: Canvas) -> None:
    for y in range(c.height):
        for x in range(c.width):
            assert c.get_pixel(x, y)[3] in (0, 255)


# --- polygon -------------------------------------------------------------------------------


def test_polygon_fill_covers_exact_bbox_and_nothing_outside() -> None:
    c = Canvas(8, 8)
    shape = PolygonShape(op="polygon", points=[(1, 1), (5, 1), (5, 4), (1, 4)], color="ink")
    draw_shape(c, shape, (0, 0), RED)
    # an axis-aligned rect polygon fills its interior plus the closed outline, so the
    # boundary ring (right column x=5 and bottom row y=4) is present too
    assert _opaque(c) == {(x, y) for y in range(1, 5) for x in range(1, 6)}
    assert c.bbox() == (1, 1, 6, 5)
    assert c.get_pixel(0, 0) == TRANS
    assert c.get_pixel(0, 3) == TRANS
    assert c.get_pixel(6, 2) == TRANS
    _assert_binary_alpha(c)


def test_polygon_fill_triangle_scanlines() -> None:
    c = Canvas(8, 8)
    shape = PolygonShape(op="polygon", points=[(0, 0), (7, 0), (3, 6)], color="ink")
    draw_shape(c, shape, (0, 0), RED)
    hit = _opaque(c)
    for y in range(6):  # every scanline below the base has pixels
        assert any((x, y) in hit for x in range(8)), f"row {y} empty"
    assert (0, 0) in hit  # vertex
    assert (7, 0) in hit  # vertex (was chopped before the outline pass)
    assert (3, 6) in hit  # apex vertex (was chopped before the outline pass)
    assert hit.intersection({(x, 6) for x in range(8)}) == {(3, 6)}  # only the apex on row 6
    assert (4, 6) not in hit
    assert c.bbox() == (0, 0, 8, 7)


def test_polygon_even_odd_hole() -> None:
    # one point list: outer rect + inner rect traced as a single path -> even-odd hole
    c = Canvas(10, 10)
    shape = PolygonShape(
        op="polygon",
        points=[(0, 0), (9, 0), (9, 9), (0, 9), (3, 3), (6, 3), (6, 6), (3, 6)],
        color="ink",
    )
    draw_shape(c, shape, (0, 0), RED)
    assert c.get_pixel(1, 1) == RED  # ring
    assert c.get_pixel(8, 8) == RED
    assert c.get_pixel(2, 2) == RED
    assert c.get_pixel(4, 4) == TRANS  # hole
    assert c.get_pixel(5, 5) == TRANS
    assert c.get_pixel(4, 0) == RED  # top band
    assert c.get_pixel(4, 9) == RED  # bottom edge present via the outline pass


def test_polygon_outline_draws_edges_only() -> None:
    c = Canvas(8, 8)
    shape = PolygonShape(op="polygon", points=[(1, 1), (6, 1), (3, 5)], color="ink", fill=False)
    draw_shape(c, shape, (0, 0), RED)
    for p in [(1, 1), (6, 1), (3, 5), (5, 2), (4, 4), (2, 3)]:  # vertices + on-edge pixels
        assert c.get_pixel(*p) == RED
    assert c.get_pixel(3, 2) == TRANS  # interior not filled
    assert c.get_pixel(0, 0) == TRANS


def test_polygon_clips_at_canvas_edges() -> None:
    c = Canvas(8, 8)
    shape = PolygonShape(op="polygon", points=[(-3, -3), (5, -3), (1, 4)], color="ink")
    draw_shape(c, shape, (0, 0), RED)
    assert c.get_pixel(1, 1) == RED  # clipped but visible part drawn
    for x, y in _opaque(c):
        assert 0 <= x < 8 and 0 <= y < 8
    _assert_binary_alpha(c)


def test_polygon_degenerate_points_draw_nothing() -> None:
    c = Canvas(4, 4)
    c.draw_polygon([(0, 0), (1, 1)], RED, fill=True)  # canvas-level guard
    assert c.array.sum() == 0


# --- arc -----------------------------------------------------------------------------------


def test_arc_full_circle_ring() -> None:
    c = Canvas(15, 15)
    shape = ArcShape(op="arc", at=(7, 7), radius=5, color="ink")  # 0..360 = full circle
    draw_shape(c, shape, (0, 0), RED)
    for x, y in [(12, 7), (2, 7), (7, 12), (7, 2)]:  # cardinal points at exactly radius 5
        assert c.get_pixel(x, y) == RED
    assert c.get_pixel(7, 7) == TRANS  # centre
    assert c.get_pixel(7, 6) == TRANS  # just inside the band
    assert c.get_pixel(7, 1) == TRANS  # just outside
    assert c.get_pixel(0, 0) == TRANS
    # every ring pixel lies in the 1px band around radius 5 (doubled coords: [81, 121])
    for x, y in _opaque(c):
        qx, qy = 2 * (x - 7), 2 * (y - 7)
        assert 81 <= qx * qx + qy * qy <= 121
    assert c.bbox() == (2, 2, 13, 13)
    _assert_binary_alpha(c)


def test_arc_full_circle_thick_ring() -> None:
    c = Canvas(15, 15)
    shape = ArcShape(op="arc", at=(7, 7), radius=4, thickness=3, color="ink")
    draw_shape(c, shape, (0, 0), RED)
    assert c.get_pixel(11, 7) == RED  # outer cardinal
    assert c.get_pixel(7, 10) == RED  # dist 3, inside band [2.5, 5.5]
    assert c.get_pixel(7, 9) == TRANS  # dist 2, inside the hole
    assert c.get_pixel(7, 13) == TRANS  # dist 6, outside the band
    for x, y in _opaque(c):
        qx, qy = 2 * (x - 7), 2 * (y - 7)
        assert 25 <= qx * qx + qy * qy <= 121


def test_arc_partial_sweep_angles() -> None:
    c = Canvas(15, 15)
    shape = ArcShape(op="arc", at=(7, 7), radius=5, start_deg=0, end_deg=90, color="ink")
    draw_shape(c, shape, (0, 0), RED)
    assert c.get_pixel(12, 7) == RED  # 0 deg = +x
    assert c.get_pixel(7, 12) == RED  # 90 deg = +y (down, clockwise in screen coords)
    assert c.get_pixel(11, 10) == RED  # ~37 deg inside the sweep, on the 1px ring
    assert c.get_pixel(2, 7) == TRANS  # 180 deg outside
    assert c.get_pixel(7, 2) == TRANS  # 270 deg outside


def test_arc_fill_pie_slice() -> None:
    c = Canvas(11, 11)
    shape = ArcShape(
        op="arc", at=(5, 5), radius=4, start_deg=0, end_deg=170, fill=True, color="ink"
    )
    draw_shape(c, shape, (0, 0), RED)
    assert c.get_pixel(5, 5) == RED  # centre of the pie
    assert c.get_pixel(9, 5) == RED  # east edge
    assert c.get_pixel(5, 9) == RED  # south edge
    assert c.get_pixel(8, 6) == RED  # ~18 deg
    assert c.get_pixel(1, 5) == TRANS  # 180 deg > end
    assert c.get_pixel(5, 1) == TRANS  # 270 deg > end


def test_arc_clips_at_canvas_edges() -> None:
    c = Canvas(8, 8)
    shape = ArcShape(op="arc", at=(-3, 4), radius=5, color="ink")
    draw_shape(c, shape, (0, 0), BLUE)
    for x, y in _opaque(c):
        assert 0 <= x < 8 and 0 <= y < 8
    _assert_binary_alpha(c)


# --- curve / bezier ------------------------------------------------------------------------


def test_curve_polyline_matches_draw_line_segments() -> None:
    c = Canvas(8, 8)
    shape = CurveShape(op="curve", points=[(0, 0), (3, 1), (6, 0)], color="ink")
    draw_shape(c, shape, (1, 1), RED)  # origin offset
    assert c.get_pixel(1, 1) == RED  # p0 + origin
    assert c.get_pixel(4, 2) == RED  # p1 + origin
    assert c.get_pixel(7, 1) == RED  # p2 + origin
    expected = Canvas(8, 8)
    expected.draw_line((1, 1), (4, 2), RED)
    expected.draw_line((4, 2), (7, 1), RED)
    assert c.equals(expected)
    assert shape_bounds(shape, (1, 1)) == (1, 1, 8, 3)


def test_curve_thickness_widens_with_distance_band() -> None:
    c = Canvas(8, 8)
    shape = CurveShape(op="curve", points=[(0, 2), (4, 2)], color="ink", thickness=3)
    draw_shape(c, shape, (0, 0), RED)
    assert c.get_pixel(2, 1) == RED
    assert c.get_pixel(2, 3) == RED
    assert c.get_pixel(5, 1) == RED  # band extends 1px past the last point (rounded cap)
    assert c.get_pixel(5, 2) == RED
    assert c.get_pixel(0, 0) == TRANS
    assert c.get_pixel(6, 2) == TRANS  # distance 2 > half-width 1.5
    assert c.get_pixel(5, 4) == TRANS
    assert shape_bounds(shape, (0, 0)) == (-1, 1, 6, 4)


def test_bezier_endpoints_and_shape() -> None:
    c = Canvas(16, 16)
    shape = BezierShape(op="bezier", p0=(1, 8), p1=(8, 1), p2=(15, 8), color="ink")
    draw_shape(c, shape, (0, 0), RED)
    assert c.get_pixel(1, 8) == RED  # p0 always included
    assert c.get_pixel(15, 8) == RED  # p2 always included
    assert c.get_pixel(8, 5) == RED  # sampled apex of the parabola
    assert c.get_pixel(8, 1) == TRANS  # control point p1 is not on the curve
    assert c.get_pixel(1, 9) == TRANS  # curve never dips below its endpoints
    hit = _opaque(c)
    assert len(hit) >= 15  # densely sampled, not just three dots
    assert all(y <= 8 for _, y in hit)


def test_curve_and_bezier_clip_at_canvas_edges() -> None:
    c = Canvas(8, 8)
    curve = CurveShape(op="curve", points=[(-5, 0), (3, 0)], color="ink")
    bez = BezierShape(op="bezier", p0=(-2, 8), p1=(4, -2), p2=(10, 8), color="ink")
    draw_shape(c, curve, (0, 0), GREEN)
    draw_shape(c, bez, (0, 0), BLUE)
    for x, y in _opaque(c):
        assert 0 <= x < 8 and 0 <= y < 8
    _assert_binary_alpha(c)


# --- thick strokes: uniform width, centred, no bias ----------------------------------------


def _column_widths(c: Canvas) -> dict[int, int]:
    def _opaque(x: int) -> int:
        return sum(1 for y in range(c.height) if c.get_pixel(x, y)[3] == 255)

    return {x: _opaque(x) for x in range(c.width)}


def test_thick_line_uniform_width_and_centered() -> None:
    # horizontal t=2 line: every column in the span is exactly 3 px, centred on the line
    c = Canvas(16, 16)
    c.draw_polyline([(0, 8), (15, 8)], RED, thickness=2)
    w = _column_widths(c)
    assert all(w[x] == 3 for x in range(16)), w
    assert c.get_pixel(8, 7) == RED and c.get_pixel(8, 8) == RED and c.get_pixel(8, 9) == RED
    assert c.get_pixel(8, 6) == TRANS and c.get_pixel(8, 10) == TRANS
    # a 45-degree t=2 line: every column width in {2, 3}, no 4-5 px lumps, no choke points
    c = Canvas(16, 16)
    c.draw_polyline([(0, 0), (15, 15)], RED, thickness=2)
    w = _column_widths(c)
    assert all(w[x] in (2, 3) for x in range(16)), w
    # odd thickness too: horizontal t=3 is 3 px (half-width 1.5 rounds inward to 1)
    c = Canvas(16, 16)
    c.draw_polyline([(0, 8), (15, 8)], RED, thickness=3)
    w = _column_widths(c)
    assert all(w[x] in (2, 3) for x in range(16)), w


def test_thick_bezier_column_widths_uniform() -> None:
    # a gentle t=2 bezier (|slope| <= 0.5): every interior column width in {2, 3}
    c = Canvas(16, 16)
    shape = BezierShape(op="bezier", p0=(1, 7), p1=(8, 5), p2=(15, 7), color="ink", thickness=2)
    draw_shape(c, shape, (0, 0), RED)
    w = _column_widths(c)
    assert w[0] == 1  # end-cap column
    assert all(w[x] in (2, 3) for x in range(1, 16)), w
    # no 1 px choke points anywhere on the stroke body (only the p0 end-cap column)
    assert all(n >= 2 for x, n in w.items() if n > 0 and x != 0)


def test_thick_bezier_symmetric_about_apex() -> None:
    # the critic's demo bezier is symmetric about x = 32; the stroke must be too
    c = Canvas(64, 64)
    shape = BezierShape(op="bezier", p0=(8, 56), p1=(32, 8), p2=(56, 56), color="ink", thickness=2)
    draw_shape(c, shape, (0, 0), RED)
    w = _column_widths(c)
    nz = [x for x in w if w[x] > 0]
    assert nz == list(range(7, 58))  # no gaps in the stroke
    assert all(w[x] == w[64 - x] for x in nz), "column widths must mirror about x=32"
    assert max(w.values()) <= 5  # true band width at slope 2 is 2*sqrt(5) ~= 4.5 px


# --- polygon fill: vertices present, symmetric ----------------------------------------------


def test_polygon_fill_includes_all_vertices() -> None:
    c = Canvas(64, 64)
    shape = PolygonShape(op="polygon", points=[(8, 8), (56, 8), (32, 52)], color="ink")
    draw_shape(c, shape, (0, 0), RED)
    hit = _opaque(c)
    assert (8, 8) in hit and (56, 8) in hit and (32, 52) in hit  # all three vertices
    assert c.bbox() == (8, 8, 57, 53)
    # mirror symmetry about x = 32: a symmetric triangle renders a symmetric silhouette
    assert all((64 - x, y) in hit for x, y in hit)


def test_polygon_fill_agrees_with_outline() -> None:
    pts = [(8, 8), (56, 8), (32, 52)]
    filled = Canvas(64, 64)
    filled.draw_polygon(pts, RED, fill=True)
    outlined = Canvas(64, 64)
    outlined.draw_polygon(pts, RED, fill=False)
    hit_f = _opaque(filled)
    hit_o = _opaque(outlined)
    assert hit_o <= hit_f  # every outline pixel is inside the filled polygon


# --- shape_bounds vs rendered bbox ---------------------------------------------------------


def test_shape_bounds_bezier_matches_rendered_bbox() -> None:
    shape = BezierShape(op="bezier", p0=(8, 56), p1=(32, 8), p2=(56, 56), color="ink", thickness=2)
    c = Canvas(80, 80)
    draw_shape(c, shape, (0, 0), RED)
    assert shape_bounds(shape, (0, 0)) == c.bbox()  # exactly tight
    # thickness 3: bounds stay within 1 px of the rendered bbox
    shape3 = BezierShape(op="bezier", p0=(8, 56), p1=(32, 8), p2=(56, 56), color="ink", thickness=3)
    c3 = Canvas(80, 80)
    draw_shape(c3, shape3, (0, 0), RED)
    sb, rb = shape_bounds(shape3, (0, 0)), c3.bbox()
    assert sb[0] <= rb[0] and sb[1] <= rb[1] and sb[2] >= rb[2] and sb[3] >= rb[3]
    assert max(rb[0] - sb[0], rb[1] - sb[1], sb[2] - rb[2], sb[3] - rb[3]) <= 1


def test_shape_bounds_arc_matches_rendered_bbox() -> None:
    # full circle: ext = (2r + t) // 2, exactly tight
    shape = ArcShape(op="arc", at=(40, 64), radius=20, thickness=3, color="ink")
    c = Canvas(100, 100)
    draw_shape(c, shape, (0, 0), RED)
    assert shape_bounds(shape, (0, 0)) == c.bbox()
    # partial sweep: bounds follow the true sweep, within 1 px
    part = ArcShape(
        op="arc", at=(40, 30), radius=20, thickness=3, start_deg=0, end_deg=270, color="ink"
    )
    c2 = Canvas(100, 100)
    draw_shape(c2, part, (0, 0), RED)
    sb, rb = shape_bounds(part, (0, 0)), c2.bbox()
    assert sb[0] <= rb[0] and sb[1] <= rb[1] and sb[2] >= rb[2] and sb[3] >= rb[3]
    assert max(rb[0] - sb[0], rb[1] - sb[1], sb[2] - rb[2], sb[3] - rb[3]) <= 1


# --- determinism ---------------------------------------------------------------------------


def test_new_ops_are_deterministic() -> None:
    shapes = [
        PolygonShape(op="polygon", points=[(0, 0), (6, 0), (3, 5)], color="ink"),
        ArcShape(op="arc", at=(5, 5), radius=4, color="ink"),
        CurveShape(op="curve", points=[(0, 0), (4, 2), (8, 0)], color="ink"),
        BezierShape(op="bezier", p0=(0, 4), p1=(4, 0), p2=(8, 4), color="ink"),
    ]
    for shape in shapes:
        a, b = Canvas(16, 16), Canvas(16, 16)
        draw_shape(a, shape, (0, 0), RED)
        draw_shape(b, shape, (0, 0), RED)
        assert a.equals(b), f"non-deterministic render for op {shape.op!r}"


# --- shape_bounds --------------------------------------------------------------------------


def test_shape_bounds_each_new_op() -> None:
    poly = PolygonShape(op="polygon", points=[(1, 2), (5, 2), (3, 6)], color="ink")
    assert shape_bounds(poly, (0, 0)) == (1, 2, 6, 7)
    arc = ArcShape(op="arc", at=(4, 4), radius=3, thickness=2, color="ink")
    assert shape_bounds(arc, (1, 0)) == (1, 0, 10, 9)  # ext = (2r + t) // 2 = 4, not r + t
    thin = CurveShape(op="curve", points=[(1, 1), (4, 3)], color="ink", thickness=1)
    assert shape_bounds(thin, (0, 0)) == (1, 1, 5, 4)
    thick = CurveShape(op="curve", points=[(1, 1), (4, 3)], color="ink", thickness=3)
    assert shape_bounds(thick, (0, 0)) == (0, 0, 6, 5)  # half-width 1.5, rounded outward
    bez = BezierShape(op="bezier", p0=(0, 0), p1=(2, 4), p2=(4, 0), color="ink")
    # true curve extrema: y peaks at 2 (t=0.5), not the control point y=4
    assert shape_bounds(bez, (1, 1)) == (1, 1, 6, 4)


# --- schema validators ---------------------------------------------------------------------


def test_polygon_requires_three_points() -> None:
    with pytest.raises(ValidationError, match="at least 3 points"):
        PolygonShape(op="polygon", points=[(0, 0), (1, 1)], color="ink")


def test_arc_rejects_negative_radius_and_zero_thickness() -> None:
    with pytest.raises(ValidationError, match="radius"):
        ArcShape(op="arc", at=(0, 0), radius=-1, color="ink")
    with pytest.raises(ValidationError, match="thickness"):
        ArcShape(op="arc", at=(0, 0), radius=3, thickness=0, color="ink")


def test_curve_requires_two_points() -> None:
    with pytest.raises(ValidationError, match="at least 2 points"):
        CurveShape(op="curve", points=[(0, 0)], color="ink")


def test_new_shapes_forbid_extra_fields() -> None:
    with pytest.raises(ValidationError):
        PolygonShape.model_validate(
            {"op": "polygon", "points": [[0, 0], [1, 1], [2, 2]], "color": "ink", "bogus": 1}
        )
    with pytest.raises(ValidationError):
        ArcShape.model_validate(
            {"op": "arc", "at": [0, 0], "radius": 2, "color": "ink", "bogus": 1}
        )
    with pytest.raises(ValidationError):
        BezierShape.model_validate(
            {"op": "bezier", "p0": [0, 0], "p1": [1, 1], "p2": [2, 2], "color": "ink", "bogus": 1}
        )


def test_spec_style_dicts_validate() -> None:
    # the exact dict shapes a YAML spec would produce
    poly = PolygonShape.model_validate(
        {"op": "polygon", "color": "ink", "points": [[0, 0], [6, 0], [3, 5]]}
    )
    assert poly.fill is True
    arc = ArcShape.model_validate(
        {"op": "arc", "color": "ink", "at": [4, 4], "radius": 3, "end_deg": 180}
    )
    assert arc.start_deg == 0 and arc.end_deg == 180 and arc.fill is False
    curve = CurveShape.model_validate({"op": "curve", "color": "ink", "points": [[0, 0], [2, 3]]})
    assert curve.thickness == 1
    bez = BezierShape.model_validate(
        {"op": "bezier", "color": "ink", "p0": [0, 0], "p1": [2, 2], "p2": [4, 0]}
    )
    assert bez.p1 == (2, 2)


# --- end-to-end: parse -> resolve -> render dispatch ---------------------------------------

_PALETTE = {
    "id": "p",
    "colors": [
        {"id": "ink", "hex": "#000000"},
        {"id": "red", "hex": "#ff0000"},
        {"id": "blue", "hex": "#0000ff"},
        {"id": "green", "hex": "#00ff00"},
    ],
}


def _doc() -> Any:
    return parse_asset_doc(
        {
            "schema_version": 1,
            "asset": {"id": "x", "type": "character", "canvas": [16, 16]},
            "palette": _PALETTE,
            "directions": ["south"],
            "mirror": {},
            "anchors": {"root": [0, 0]},
            "regions": {
                "body": {
                    "anchor": "root",
                    "layer": 0,
                    "shapes": [
                        {"op": "polygon", "color": "red", "points": [[0, 0], [4, 0], [2, 3]]},
                        {"op": "arc", "color": "blue", "at": [8, 6], "radius": 3},
                        {"op": "curve", "color": "green", "points": [[12, 2], [14, 4]]},
                        {"op": "bezier", "color": "ink", "p0": [6, 2], "p1": [8, 0], "p2": [10, 2]},
                    ],
                }
            },
            "direction_overrides": {},
            "animations": {
                "idle": {
                    "loop": True,
                    "frames": [{"duration_ms": 100, "events": [], "transforms": {}}],
                }
            },
            "export": {},
            "validation": {},
        }
    )


def test_all_new_ops_render_through_draw_shape_dispatch() -> None:
    doc = _doc()
    palette = resolve_palette(doc.palette)
    frame = resolve_frames(doc)[0]
    canvas = LocalRenderBackend().render_frame(doc, frame, palette)
    assert [s.op for s in doc.regions["body"].shapes] == ["polygon", "arc", "curve", "bezier"]
    assert canvas.get_pixel(1, 1) == palette.rgba("red")  # polygon fill
    assert canvas.get_pixel(11, 6) == palette.rgba("blue")  # arc east cardinal
    assert canvas.get_pixel(13, 3) == palette.rgba("green")  # curve segment pixel
    assert canvas.get_pixel(6, 2) == palette.rgba("ink")  # bezier p0
    assert canvas.get_pixel(10, 2) == palette.rgba("ink")  # bezier p2
    _assert_binary_alpha(canvas)


def test_all_new_ops_render_is_deterministic_end_to_end() -> None:
    doc = _doc()
    palette = resolve_palette(doc.palette)
    frame = resolve_frames(doc)[0]
    first = LocalRenderBackend().render_frame(doc, frame, palette)
    second = LocalRenderBackend().render_frame(doc, frame, palette)
    assert first.equals(second)
