"""Exact-pixel tests for the local render backend, compositor, and layer planning."""

from __future__ import annotations

from typing import Any

import pytest

from pixel_forge.animation import resolve_frames
from pixel_forge.domain.palette import resolve_palette
from pixel_forge.errors import ForgeError, PaletteError, RenderError
from pixel_forge.rendering.local import (
    LocalRenderBackend,
    render_asset_frames,
    render_terrain_tiles,
)
from pixel_forge.schemas import parse_asset_doc

RED = (255, 0, 0, 255)
BLUE = (0, 0, 255, 255)

_PALETTE = {
    "id": "p",
    "colors": [{"id": "red", "hex": "#ff0000"}, {"id": "blue", "hex": "#0000ff"}],
}


def _doc(
    *,
    canvas: list[int] | None = None,
    directions: list[str] | None = None,
    mirror: dict[str, str] | None = None,
    anchors: dict[str, Any] | None = None,
    regions: dict[str, Any] | None = None,
    direction_overrides: dict[str, Any] | None = None,
    transforms: dict[str, Any] | None = None,
) -> Any:
    return parse_asset_doc(
        {
            "schema_version": 1,
            "asset": {"id": "x", "type": "character", "canvas": canvas or [16, 16]},
            "palette": _PALETTE,
            "directions": directions or ["south"],
            "mirror": mirror or {},
            "anchors": anchors or {"root": [0, 0]},
            "regions": regions
            or {
                "body": {
                    "anchor": "root",
                    "layer": 0,
                    "shapes": [{"op": "rect", "color": "red", "at": [0, 0], "size": [2, 2]}],
                }
            },
            "direction_overrides": direction_overrides or {},
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


def _terrain_doc(*, tiles: dict[str, Any] | None = None) -> Any:
    return parse_asset_doc(
        {
            "schema_version": 1,
            "asset": {"id": "t", "type": "terrain", "canvas": [8, 8]},
            "palette": _PALETTE,
            "export": {},
            "validation": {},
            "tiles": tiles
            or {
                "grass": {
                    "size": [8, 8],
                    "anchors": {"root": [1, 1]},
                    "regions": {
                        "fill": {
                            "anchor": "root",
                            "layer": 0,
                            "shapes": [
                                {"op": "rect", "color": "red", "at": [0, 0], "size": [4, 4]}
                            ],
                        }
                    },
                }
            },
        }
    )


def _render_south(doc: Any) -> Any:
    palette = resolve_palette(doc.palette)
    frame = resolve_frames(doc)[0]
    return LocalRenderBackend().render_frame(doc, frame, palette)


# --- layer ordering ------------------------------------------------------------------------


def test_high_layer_overpaints_low_layer() -> None:
    doc = _doc(
        regions={
            "low": {
                "anchor": "root",
                "layer": 0,
                "shapes": [{"op": "rect", "color": "red", "at": [0, 0], "size": [4, 4]}],
            },
            "high": {
                "anchor": "root",
                "layer": 1,
                "shapes": [{"op": "rect", "color": "blue", "at": [0, 0], "size": [2, 2]}],
            },
        }
    )
    canvas = _render_south(doc)
    assert canvas.get_pixel(0, 0) == BLUE
    assert canvas.get_pixel(3, 3) == RED


# --- visible ---------------------------------------------------------------------------------


def test_visible_false_removes_region() -> None:
    doc = _doc(transforms={"body": {"visible": False}})
    canvas = _render_south(doc)
    assert canvas.bbox() is None


# --- offset --------------------------------------------------------------------------------


def test_offset_moves_region_by_exact_pixels() -> None:
    doc = _doc(
        regions={
            "body": {
                "anchor": "root",
                "layer": 0,
                "shapes": [{"op": "pixel", "color": "red", "at": [0, 0]}],
            }
        },
        transforms={"body": {"offset": [3, 5]}},
    )
    canvas = _render_south(doc)
    assert canvas.bbox() == (3, 5, 4, 6)
    assert canvas.get_pixel(3, 5) == RED


# --- color_swap ------------------------------------------------------------------------------


def test_color_swap_changes_rendered_color() -> None:
    doc = _doc(transforms={"body": {"color_swap": {"red": "blue"}}})
    canvas = _render_south(doc)
    assert canvas.get_pixel(0, 0) == BLUE


def test_color_swap_unknown_target_raises() -> None:
    doc = _doc(transforms={"body": {"color_swap": {"red": "nope"}}})
    with pytest.raises(PaletteError):
        _render_south(doc)


# --- scale_size --------------------------------------------------------------------------------


def test_scale_size_grows_symmetrically_about_centre() -> None:
    doc = _doc(
        regions={
            "body": {
                "anchor": "root",
                "layer": 0,
                "shapes": [{"op": "rect", "color": "red", "at": [2, 2], "size": [4, 4]}],
            }
        },
        transforms={"body": {"scale_size": [2, 2]}},
    )
    canvas = _render_south(doc)
    # +2/+2 even growth: 1px added on every side.
    assert canvas.bbox() == (1, 1, 7, 7)


def test_scale_size_odd_growth_extends_positive_side_only() -> None:
    doc = _doc(
        regions={
            "body": {
                "anchor": "root",
                "layer": 0,
                "shapes": [{"op": "rect", "color": "red", "at": [2, 2], "size": [4, 4]}],
            }
        },
        transforms={"body": {"scale_size": [1, 0]}},
    )
    canvas = _render_south(doc)
    # +1 width, floor(1//2)=0: left edge unchanged, right edge extends by 1.
    assert canvas.bbox() == (2, 2, 7, 6)


def test_scale_size_shrink_below_1px_raises() -> None:
    doc = _doc(
        regions={
            "body": {
                "anchor": "root",
                "layer": 0,
                "shapes": [{"op": "rect", "color": "red", "at": [2, 2], "size": [1, 4]}],
            }
        },
        transforms={"body": {"scale_size": [-2, 0]}},
    )
    with pytest.raises(RenderError, match="body"):
        _render_south(doc)


def test_scale_size_ignored_on_pixel_shape() -> None:
    doc = _doc(
        regions={
            "body": {
                "anchor": "root",
                "layer": 0,
                "shapes": [{"op": "pixel", "color": "red", "at": [0, 0]}],
            }
        },
        transforms={"body": {"scale_size": [5, 5]}},
    )
    canvas = _render_south(doc)
    assert canvas.bbox() == (0, 0, 1, 1)


# --- mirroring -------------------------------------------------------------------------------


def _mirror_doc(*, unsafe: bool, direction_overrides: dict[str, Any] | None = None) -> Any:
    return _doc(
        canvas=[16, 16],
        directions=["east", "west"],
        mirror={"west": "east"},
        anchors={"root": [0, 0], "hand": [12, 4]},
        regions={
            "safe": {
                "anchor": "root",
                "layer": 0,
                "mirror_safe": True,
                "shapes": [{"op": "rect", "color": "red", "at": [1, 1], "size": [2, 2]}],
            },
            "flag": {
                "anchor": "hand",
                "layer": 1,
                "mirror_safe": not unsafe,
                "shapes": [{"op": "pixel", "color": "blue", "at": [0, 0]}],
            },
        },
        direction_overrides=direction_overrides or {},
    )


def test_mirrored_direction_equals_mirror_x_of_source_when_all_safe() -> None:
    doc = _mirror_doc(unsafe=False)
    palette = resolve_palette(doc.palette)
    frames = resolve_frames(doc)
    east = next(f for f in frames if f.direction == "east")
    west = next(f for f in frames if f.direction == "west")
    backend = LocalRenderBackend()
    east_canvas = backend.render_frame(doc, east, palette)
    west_canvas = backend.render_frame(doc, west, palette)
    assert west_canvas.equals(east_canvas.mirror_x())


def test_mirror_safe_false_region_stays_unmirrored() -> None:
    doc = _mirror_doc(unsafe=True)
    palette = resolve_palette(doc.palette)
    frames = resolve_frames(doc)
    east = next(f for f in frames if f.direction == "east")
    west = next(f for f in frames if f.direction == "west")
    backend = LocalRenderBackend()
    east_canvas = backend.render_frame(doc, east, palette)
    west_canvas = backend.render_frame(doc, west, palette)

    # The mirror-safe "safe" region mirrors as usual.
    canvas_w = doc.asset.canvas[0]
    assert west_canvas.get_pixel(canvas_w - 1 - 1, 1) == RED  # mirrored copy of (1,1)
    # The mirror-safe region no longer occupies its own (unmirrored) position on west.
    assert west_canvas.get_pixel(1, 1) == (0, 0, 0, 0)

    # The unsafe "flag" region's shapes are drawn unflipped, but its anchor ("hand" at
    # [12, 4]) is still mirrored so it stays attached to the flipped body: (12,4) on
    # the source direction, (canvas_w - 1 - 12, 4) = (3, 4) on the mirrored one.
    assert east_canvas.get_pixel(12, 4) == BLUE
    assert west_canvas.get_pixel(3, 4) == BLUE
    # It must not be left behind at the unmirrored anchor position.
    assert west_canvas.get_pixel(12, 4) == (0, 0, 0, 0)


def test_mirror_unsafe_region_inherited_override_offset_x_negated() -> None:
    # "east" (the mirror source) authors an offset for the unsafe "flag" region;
    # "west" has no override of its own, so it inherits east's — but only the x
    # component of that inherited offset should flip, to keep tracking the mirrored
    # anchor.
    doc = _mirror_doc(unsafe=True, direction_overrides={"east": {"flag": {"offset": [2, -1]}}})
    palette = resolve_palette(doc.palette)
    frames = resolve_frames(doc)
    east = next(f for f in frames if f.direction == "east")
    west = next(f for f in frames if f.direction == "west")
    backend = LocalRenderBackend()
    east_canvas = backend.render_frame(doc, east, palette)
    west_canvas = backend.render_frame(doc, west, palette)

    # East (source, authored): anchor (12,4) + offset (2,-1) = (14,3).
    assert east_canvas.get_pixel(14, 3) == BLUE
    # West (inherited, mirrored anchor (3,4) + negated-x offset (-2,-1)) = (1,3).
    assert west_canvas.get_pixel(1, 3) == BLUE


def test_mirror_unsafe_region_authored_override_used_verbatim() -> None:
    # "west" authors its own override for the unsafe "flag" region: used exactly as
    # written, not negated, since the author was describing "west" directly.
    doc = _mirror_doc(unsafe=True, direction_overrides={"west": {"flag": {"offset": [5, 2]}}})
    palette = resolve_palette(doc.palette)
    frames = resolve_frames(doc)
    west = next(f for f in frames if f.direction == "west")
    backend = LocalRenderBackend()
    west_canvas = backend.render_frame(doc, west, palette)

    # Mirrored anchor (3,4) + authored offset (5,2) = (8,6), not negated.
    assert west_canvas.get_pixel(8, 6) == BLUE


# --- clipping ----------------------------------------------------------------------------------


def test_off_canvas_region_clips_without_raising() -> None:
    doc = _doc(
        canvas=[4, 4],
        anchors={"root": [3, 3]},
        regions={
            "body": {
                "anchor": "root",
                "layer": 0,
                "shapes": [{"op": "rect", "color": "red", "at": [0, 0], "size": [4, 4]}],
            }
        },
    )
    canvas = _render_south(doc)
    assert canvas.bbox() == (3, 3, 4, 4)


# --- determinism -------------------------------------------------------------------------------


def test_render_is_deterministic() -> None:
    doc = _doc(
        regions={
            "body": {
                "anchor": "root",
                "layer": 0,
                "shapes": [
                    {"op": "ellipse", "color": "red", "at": [1, 1], "size": [6, 6]},
                    {"op": "line", "color": "blue", "start": [0, 0], "end": [7, 7]},
                ],
            }
        }
    )
    first = _render_south(doc)
    second = _render_south(doc)
    assert first.equals(second)


def test_render_asset_frames_keys_and_order_match_resolve_frames() -> None:
    doc = _doc(directions=["south"])
    result = render_asset_frames(doc)
    expected_keys = [(f.animation, f.direction, f.index) for f in resolve_frames(doc)]
    assert list(result.keys()) == expected_keys


# --- render_tile -----------------------------------------------------------------------------


def test_render_tile_on_terrain_doc() -> None:
    doc = _terrain_doc()
    palette = resolve_palette(doc.palette)
    canvas = LocalRenderBackend().render_tile(doc, "grass", palette)
    assert canvas.width == 8
    assert canvas.height == 8
    assert canvas.get_pixel(1, 1) == RED
    assert canvas.bbox() == (1, 1, 5, 5)


def test_render_tile_unknown_id_raises() -> None:
    doc = _terrain_doc()
    palette = resolve_palette(doc.palette)
    with pytest.raises(ForgeError):
        LocalRenderBackend().render_tile(doc, "missing", palette)


def test_render_terrain_tiles_keyed_by_tile_id() -> None:
    doc = _terrain_doc()
    result = render_terrain_tiles(doc)
    assert list(result.keys()) == ["grass"]
