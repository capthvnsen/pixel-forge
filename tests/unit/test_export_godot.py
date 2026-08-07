"""Tests for the neutral Godot import manifest exporter."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from pixel_forge.animation.resolver import resolve_frames
from pixel_forge.errors import ExportError
from pixel_forge.exporters.godot import (
    build_animation_player,
    build_animation_player_export,
    build_godot_manifest,
    build_sprite_frames,
    build_tileset,
    write_godot_manifest,
)
from pixel_forge.exporters.godot.spriteframes import derive_fps, duration_frames_for
from pixel_forge.exporters.godot.tileset import peering_bit_name
from pixel_forge.rendering.canvas import Canvas
from pixel_forge.rendering.sheet import SheetCell, build_atlas, build_sprite_sheet
from pixel_forge.schemas import parse_asset_doc

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "golden" / "fixtures" / "godot"


def _prop_doc(*, anchors: dict[str, Any] | None = None) -> Any:
    return parse_asset_doc(
        {
            "schema_version": 1,
            "asset": {"id": "beacon", "type": "prop", "canvas": [8, 8], "baseline_y": 6},
            "palette": {"id": "p", "colors": [{"id": "glow", "hex": "#ffcc00"}]},
            "directions": ["front"],
            "mirror": {},
            "anchors": anchors if anchors is not None else {"root": [0, 0], "feet": [4, 7]},
            "regions": {
                "base": {"anchor": "root", "layer": 0, "shapes": []},
                "vane": {"anchor": "root", "layer": 1, "shapes": []},
            },
            "animations": {
                "spin": {
                    "loop": True,
                    "frames": [
                        {"duration_ms": 100, "events": ["tick"], "transforms": {"vane": {}}},
                        {
                            "duration_ms": 100,
                            "events": [],
                            "transforms": {"vane": {"offset": [1, 0]}},
                        },
                    ],
                    "procedural": {
                        "shader": "glow_pulse",
                        "params": {"speed": 1.5},
                        "target_region": "vane",
                    },
                }
            },
            "export": {},
            "validation": {},
        }
    )


def _terrain_doc() -> Any:
    return parse_asset_doc(
        {
            "schema_version": 1,
            "asset": {"id": "forest", "type": "terrain", "canvas": [16, 16]},
            "palette": {"id": "p", "colors": [{"id": "grass", "hex": "#3a9b3a"}]},
            "tiles": {
                "grass": {"size": [8, 8], "regions": {}, "anchors": {}},
                "dirt": {
                    "size": [8, 8],
                    "regions": {},
                    "anchors": {},
                    "collision": "solid",
                    "navigation": True,
                },
                # Dedicated animation-only frames, distinct from grass/dirt -- an
                # animated tile's frames must not double as independently terrain-bearing
                # tiles, since only the base frame survives as its own `tiles` entry.
                "water_1": {"size": [8, 8], "regions": {}, "anchors": {}},
                "water_2": {"size": [8, 8], "regions": {}, "anchors": {}},
            },
            "terrain_sets": {"ground": {"mode": "corners_and_edges", "tiles": ["grass", "dirt"]}},
            "transitions": [
                {"from_terrain": "grass", "to_terrain": "dirt", "tile_id": "dirt", "mask": mask}
                for mask in ("N", "NE", "E", "SW")
            ],
            "animated_tiles": {
                "water_flow": {
                    "frames": ["water_1", "water_2"],
                    "frame_duration_ms": 150,
                    "loop": True,
                }
            },
            "sample_map": {
                "size": [2, 2],
                # "water_2" (a non-base animation frame) exercises resolution to the
                # base frame's coords -- see test_sample_map_coords_resolved.
                "layers": {"ground": [["grass", "dirt"], ["dirt", "water_2"]]},
            },
            "export": {},
            "validation": {},
        }
    )


def _prop_sheet(doc: Any) -> tuple[Any, list[Any]]:
    frames = resolve_frames(doc)
    canvas_size = doc.asset.canvas
    sheet = build_sprite_sheet([(f, Canvas(*canvas_size)) for f in frames], canvas_size)
    return sheet, frames


def _terrain_atlas(doc: Any) -> dict[str, SheetCell]:
    tw, th = next(iter(doc.tiles.values())).size
    _, cells = build_atlas({tile_id: Canvas(tw, th) for tile_id in doc.tiles})
    return cells


# --- fps derivation --------------------------------------------------------------------------


def test_derive_fps_equal_durations() -> None:
    assert derive_fps([100, 100, 100]) == 10.0
    assert duration_frames_for([100, 100, 100], 10.0) == [1.0, 1.0, 1.0]


def test_derive_fps_unequal_durations() -> None:
    assert derive_fps([100, 200, 100]) == 10.0
    assert duration_frames_for([100, 200, 100], 10.0) == [1.0, 2.0, 1.0]


def test_derive_fps_gcd_one_is_capped() -> None:
    # gcd(100, 101) == 1 -> uncapped fps would be 1000; capped to the default 60.
    assert derive_fps([100, 101]) == 60.0
    assert duration_frames_for([100, 101], 60.0) == [6.0, 6.06]


def test_derive_fps_rejects_empty_or_non_positive() -> None:
    with pytest.raises(ExportError):
        derive_fps([])
    with pytest.raises(ExportError):
        derive_fps([100, 0])


# --- fps parity with the GDScript plugin ------------------------------------------------
#
# importer.gd::_derive_fps must produce the same number as derive_fps for every manifest
# the exporter can emit: the plugin deliberately replicates the formula rather than
# reading a computed `fps` from the manifest (see the integration note atop
# spriteframes.py). This is the bit-for-bit parity pin -- GDScript floats are IEEE-754
# doubles, identical to Python floats, so `minf(1000.0 / float(gcd), max_fps)` must equal
# `min(1000.0 / gcd, max_fps)` exactly. Duration sets below are the real ones from the
# shipped examples (crawler idle, beacon active, engineer attack, sporeling death, ...)
# plus the golden-fixture durations and the cap/edge cases derive_fps itself tests.


def _gdscript_derive_fps(durations: Sequence[int], max_fps: float = 60.0) -> float:
    """Bit-for-bit port of `importer.gd::_derive_fps` (durations from JSON parse as
    floats in GDScript, hence the explicit `int()` casts mirroring the plugin)."""
    if not durations:
        return max_fps
    gcd_ms = int(durations[0])
    for d in durations:
        gcd_ms = math.gcd(gcd_ms, int(d))
    if gcd_ms <= 0:
        return max_fps
    return min(1000.0 / gcd_ms, max_fps)


_EXAMPLE_DURATION_SETS: list[list[int]] = [
    [150, 150],  # crawler idle (the canonical 2-frame 150ms idle)
    [100, 100, 100, 100, 100, 100],  # beacon active
    [400, 400, 400, 400],  # beacon idle
    [90, 90, 90, 90],  # crawler attack / move
    [200, 200],  # crawler telegraph
    [120, 90, 120, 140],  # engineer attack (gcd 10 -> capped at 60)
    [160, 160, 160, 160],  # engineer idle
    [260, 260, 260, 260],  # rune_chest idle
    [90, 90, 90, 220],  # rune_chest opening
    [90, 90, 120],  # sporeling attack
    [100, 120, 140, 200],  # sporeling death
    [80, 120],  # sporeling impact
    [200, 200, 200, 200],  # sporeling / vanguard idle
    [140, 140, 160],  # sporeling telegraph
    [110, 110, 110, 110],  # vanguard walk
    [220, 220, 220, 220],  # warden idle
    [100, 100],  # golden beacon spin fixture
]


@pytest.mark.parametrize("durations", _EXAMPLE_DURATION_SETS)
def test_plugin_fps_formula_matches_derive_fps_bit_for_bit(durations: list[int]) -> None:
    assert _gdscript_derive_fps(durations) == derive_fps(durations)
    # And the per-frame Godot duration multipliers agree too.
    fps = derive_fps(durations)
    assert [d * fps / 1000.0 for d in durations] == duration_frames_for(durations, fps)


# --- Animation.length parity with the GDScript plugin -----------------------------------
#
# importer.gd::_import_animation_player sets `animation.length` from the manifest's
# `total_duration_ms` (`length = total_duration_ms / 1000`, in seconds). This pins the
# truncation fix: for every real example duration set the total exceeds the last
# keyframe's *start* time, which is what the plugin previously (incorrectly) used.


def _gdscript_animation_length(total_duration_ms: int) -> float:
    """Bit-for-bit port of the plugin's length derivation for a manifest carrying
    `total_duration_ms` (JSON numbers parse as float in GDScript, so `float()` the
    value before dividing)."""
    return max(0.001, float(total_duration_ms) / 1000.0)


@pytest.mark.parametrize(
    ("durations", "expected_length"),
    [
        ([400, 400, 400, 400], 1.6),  # beacon idle: 1.6s spec (was 1.2)
        ([100, 100, 100, 100, 100, 100], 0.6),  # beacon active (was 0.5)
        ([90, 90, 90, 220], 0.49),  # rune_chest opening (was 0.27)
        ([150, 150], 0.30),  # crawler idle (was 0.15)
        ([260, 260, 260, 260], 1.04),  # rune_chest idle (colour-swap-only)
    ],
)
def test_plugin_animation_length_uses_total_duration_not_last_keyframe_start(
    durations: list[int], expected_length: float
) -> None:
    total_ms = sum(durations)
    # The old, truncating behaviour the plugin no longer uses:
    last_keyframe_start = total_ms - durations[-1]
    assert last_keyframe_start < total_ms
    # The fixed behaviour:
    assert _gdscript_animation_length(total_ms) == expected_length


# --- build_sprite_frames -----------------------------------------------------------------------


def test_build_sprite_frames_names_loop_and_rects() -> None:
    doc = _prop_doc()
    sheet, frames = _prop_sheet(doc)
    result = build_sprite_frames(doc, sheet, frames)

    assert set(result) == {"spin_front"}
    anim = result["spin_front"]
    assert anim.loop is True
    assert [f.duration_ms for f in anim.frames] == [100, 100]
    assert (anim.frames[0].rect.w, anim.frames[0].rect.h) == (8, 8)


def test_events_attach_to_correct_frame_index_via_manifest() -> None:
    doc = _prop_doc()
    sheet, frames = _prop_sheet(doc)
    manifest = build_godot_manifest(
        doc, sheet=sheet, texture_paths={"atlas": "beacon/atlas.png"}, spec_hash="x", frames=frames
    )
    assert manifest.events["spin"] == [["tick"], []]


# --- build_tileset -----------------------------------------------------------------------------


def test_build_tileset_atlas_coords_and_terrain_transitions() -> None:
    doc = _terrain_doc()
    cells = _terrain_atlas(doc)
    tileset = build_tileset(doc, cells, "forest/atlas.png")

    # "water_2" (the animation's non-base frame) does not get its own tiles entry.
    coords = {t.tile_id: (t.x, t.y) for t in tileset.tiles}
    assert coords == {"dirt": (0, 0), "grass": (1, 0), "water_1": (2, 0)}
    assert tileset.tile_size == (8, 8)
    assert tileset.collision_tiles == ["dirt"]
    assert tileset.navigation_tiles == ["dirt"]
    assert tileset.occlusion_tiles == []
    assert tileset.terrain_sets["ground"].tiles == ["grass", "dirt"]
    assert [t.mask for t in tileset.transitions] == ["N", "NE", "E", "SW"]


def test_build_tileset_misaligned_cell_raises() -> None:
    doc = _terrain_doc()
    cells = {
        "grass": SheetCell(direction="grass", animation="grass", index=0, x=0, y=0, w=8, h=8),
        "dirt": SheetCell(direction="dirt", animation="dirt", index=0, x=3, y=0, w=8, h=8),
    }
    with pytest.raises(ExportError):
        build_tileset(doc, cells, "forest/atlas.png")


# --- terrain peering-bit mapping ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("mask", "expected"),
    [
        ("N", "top_side"),
        ("NE", "top_right_corner"),
        ("E", "right_side"),
        ("SW", "bottom_left_corner"),
    ],
)
def test_peering_bit_name(mask: str, expected: str) -> None:
    assert peering_bit_name(mask) == expected


def test_peering_bit_name_unknown_raises() -> None:
    with pytest.raises(ExportError):
        peering_bit_name("XX")


def test_terrain_bits_resolved_for_n_ne_e_sw() -> None:
    doc = _terrain_doc()
    cells = _terrain_atlas(doc)
    tileset = build_tileset(doc, cells, "forest/atlas.png")

    assert tileset.terrain_bits["dirt"] == {
        "top_side": "dirt",
        "top_right_corner": "dirt",
        "right_side": "dirt",
        "bottom_left_corner": "dirt",
    }


# --- animated tiles ------------------------------------------------------------------------------


def test_animated_tile_frame_coords_resolved() -> None:
    doc = _terrain_doc()
    cells = _terrain_atlas(doc)
    tileset = build_tileset(doc, cells, "forest/atlas.png")

    water = tileset.animated_tiles["water_flow"]
    assert [(f.tile_id, f.x, f.y) for f in water.frames] == [("water_1", 2, 0), ("water_2", 3, 0)]
    assert water.frame_duration_ms == 150
    assert water.loop is True


def test_animated_tile_appears_exactly_once_in_tiles() -> None:
    doc = _terrain_doc()
    cells = _terrain_atlas(doc)
    tileset = build_tileset(doc, cells, "forest/atlas.png")

    tile_ids = [t.tile_id for t in tileset.tiles]
    assert tile_ids.count("water_1") == 1
    assert "water_2" not in tile_ids


def test_collision_lists_resolve_animation_frames_to_base_tile() -> None:
    """A collision/navigation/occlusion flag on a non-base animation frame must collapse
    onto the animated tile's base frame -- the only id that is a real Godot tile. This
    mirrors the sample_map resolution: the plugin can then stamp every listed id blindly
    without re-deriving the animated-tile mapping."""
    doc = parse_asset_doc(
        {
            "schema_version": 1,
            "asset": {"id": "forest", "type": "terrain", "canvas": [16, 16]},
            "palette": {"id": "p", "colors": [{"id": "grass", "hex": "#3a9b3a"}]},
            "tiles": {
                "grass": {"size": [8, 8], "regions": {}, "anchors": {}},
                "water_1": {
                    "size": [8, 8],
                    "regions": {},
                    "anchors": {},
                    "collision": "solid",
                    "occlusion": True,
                },
                "water_2": {
                    "size": [8, 8],
                    "regions": {},
                    "anchors": {},
                    "collision": "solid",
                    "navigation": True,
                    "occlusion": True,
                },
            },
            "terrain_sets": {},
            "transitions": [],
            "animated_tiles": {
                "water_flow": {"frames": ["water_1", "water_2"], "frame_duration_ms": 150}
            },
            "export": {},
            "validation": {},
        }
    )
    cells = _terrain_atlas(doc)
    tileset = build_tileset(doc, cells, "forest/atlas.png")

    # water_2's flags (collision/navigation/occlusion) land on water_1, deduped and sorted.
    assert tileset.collision_tiles == ["water_1"]
    assert tileset.navigation_tiles == ["water_1"]
    assert tileset.occlusion_tiles == ["water_1"]


def test_animated_tile_non_contiguous_frames_raise() -> None:
    doc = _terrain_doc()
    cells = dict(_terrain_atlas(doc))
    # Move "water_2" two cells further right so it's no longer adjacent to the base
    # frame "water_1" -- the exact bug this whole feature exists to catch.
    base = cells["water_2"]
    cells["water_2"] = SheetCell(
        direction=base.direction,
        animation=base.animation,
        index=base.index,
        x=base.x + base.w * 2,
        y=base.y,
        w=base.w,
        h=base.h,
    )
    with pytest.raises(ExportError, match="water_flow"):
        build_tileset(doc, cells, "forest/atlas.png")


def test_animated_tile_unknown_frame_tile_raises() -> None:
    doc = _terrain_doc()
    cells = {k: v for k, v in _terrain_atlas(doc).items() if k != "water_2"}  # unknown frame ref
    with pytest.raises(ExportError):
        build_tileset(doc, cells, "forest/atlas.png")


# --- sample map -----------------------------------------------------------------------------------


def test_sample_map_coords_resolved() -> None:
    doc = _terrain_doc()
    cells = _terrain_atlas(doc)
    tileset = build_tileset(doc, cells, "forest/atlas.png")

    assert tileset.sample_map is not None
    assert tileset.sample_map.size == (2, 2)
    # Cell [1][1] names "water_2", the animation's non-base frame -- it must resolve to
    # "water_1"'s coords (2, 0), the only coordinate Godot knows as a real tile.
    assert tileset.sample_map.layers["ground"] == [[(1, 0), (0, 0)], [(0, 0), (2, 0)]]


def test_sample_map_unknown_tile_raises() -> None:
    doc = _terrain_doc()
    cells = _terrain_atlas(doc)
    del cells["dirt"]
    with pytest.raises(ExportError):
        build_tileset(doc, cells, "forest/atlas.png")


# --- build_animation_player --------------------------------------------------------------------


def test_animation_player_skips_static_region_and_tracks_moving_one() -> None:
    doc = _prop_doc()
    frames = resolve_frames(doc)
    tracks = build_animation_player(doc, frames)

    node_paths = {t.node_path for t in tracks}
    assert node_paths == {"spin/vane"}

    track = tracks[0]
    assert track.property == "position"
    assert [(kf.time_ms, kf.value) for kf in track.keyframes] == [
        (0, (0, 0)),
        (100, (1, 0)),
    ]


def test_animation_player_two_frame_150ms_idle_track_shape() -> None:
    """The canonical 2-frame 150ms idle: cumulative `time_ms` keyframes are the
    frames' *start* times (0.0 / 0.15s in Godot), and the animation's true total
    duration — 2 x 150ms = 300ms — is carried separately in the `animations`
    entry so the plugin sets `Animation.length = 0.30` instead of truncating to
    the last keyframe's start (0.15). The bug this pins: keyframe times alone
    can never recover the final frame's hold."""
    doc = parse_asset_doc(
        {
            "schema_version": 1,
            "asset": {"id": "crawler", "type": "prop", "canvas": [8, 8]},
            "palette": {"id": "p", "colors": [{"id": "c", "hex": "#ffcc00"}]},
            "directions": ["east"],
            "mirror": {},
            "anchors": {"root": [0, 0]},
            "regions": {
                "body": {"anchor": "root", "layer": 0, "shapes": []},
                "tail": {"anchor": "root", "layer": 1, "shapes": []},
            },
            "animations": {
                "idle": {
                    "loop": True,
                    "frames": [
                        {"duration_ms": 150, "transforms": {"tail": {}}},
                        {"duration_ms": 150, "transforms": {"tail": {"offset": [1, 0]}}},
                    ],
                }
            },
            "export": {},
            "validation": {},
        }
    )
    frames = resolve_frames(doc)
    tracks = build_animation_player(doc, frames)

    assert {t.node_path for t in tracks} == {"idle/tail"}
    track = tracks[0]
    assert track.property == "position"
    # Cumulative integer ms: (0, 150) -- the plugin divides by 1000 for Godot seconds.
    assert [(kf.time_ms, kf.value) for kf in track.keyframes] == [
        (0, (0, 0)),
        (150, (1, 0)),
    ]
    # 2-element value arrays are exactly what _to_variant() turns into Vector2.
    assert all(len(kf.value) == 2 for kf in track.keyframes)

    # The true total (0.30s) comes from the animations metadata, NOT the last
    # keyframe's start time (0.15s) -- the truncation bug this suite pins.
    export = build_animation_player_export(doc, frames)
    idle = export.animations["idle"]
    assert idle.total_duration_ms == 300
    assert idle.loop is True
    assert {t.node_path for t in export.tracks} == {"idle/tail"}


def test_animation_player_export_total_duration_includes_last_frame_hold() -> None:
    """`total_duration_ms` is the exact sum of frame durations. For the 4-frame
    90/90/90/220ms opening (the real rune_chest shape) the last keyframe starts at
    270ms but the animation is 490ms -- asserting both pins the truncation bug."""
    doc = parse_asset_doc(
        {
            "schema_version": 1,
            "asset": {"id": "chest", "type": "prop", "canvas": [8, 8]},
            "palette": {"id": "p", "colors": [{"id": "c", "hex": "#ffcc00"}]},
            "directions": ["south"],
            "mirror": {},
            "anchors": {"root": [0, 0]},
            "regions": {
                "lid": {"anchor": "root", "layer": 0, "shapes": []},
                "rune": {"anchor": "root", "layer": 1, "shapes": []},
            },
            "animations": {
                "opening": {
                    "loop": False,
                    "frames": [
                        {"duration_ms": 90, "transforms": {"lid": {"offset": [0, 0]}}},
                        {"duration_ms": 90, "transforms": {"lid": {"offset": [0, -3]}}},
                        {"duration_ms": 90, "transforms": {"lid": {"offset": [0, -6]}}},
                        {"duration_ms": 220, "transforms": {"lid": {"offset": [0, -10]}}},
                    ],
                }
            },
            "export": {},
            "validation": {},
        }
    )
    frames = resolve_frames(doc)
    export = build_animation_player_export(doc, frames)

    opening = export.animations["opening"]
    assert opening.total_duration_ms == 490
    assert opening.loop is False
    # The plugin's length math: total_duration_ms / 1000 == 0.49s.
    assert opening.total_duration_ms / 1000.0 == 0.49
    # The last keyframe's start time -- what the plugin used to truncate to.
    last_keyframe_start = max(kf.time_ms for t in export.tracks for kf in t.keyframes)
    assert last_keyframe_start == 270
    assert last_keyframe_start < opening.total_duration_ms


def test_animation_player_export_emits_metadata_for_color_swap_only_animation() -> None:
    """A region driven purely by `color_swap` produces no position/visible track
    (the exporter has no palette access and the keyframe schema can't carry a
    Color), so the animation would previously vanish. The `animations` entry is
    still emitted with the true total + loop, so the plugin builds a present,
    correctly-timed, looping zero-track Animation."""
    doc = parse_asset_doc(
        {
            "schema_version": 1,
            "asset": {"id": "chest", "type": "prop", "canvas": [8, 8]},
            "palette": {"id": "p", "colors": [{"id": "c", "hex": "#ffcc00"}]},
            "directions": ["south"],
            "mirror": {},
            "anchors": {"root": [0, 0]},
            "regions": {
                "rune": {"anchor": "root", "layer": 0, "shapes": []},
            },
            "animations": {
                "idle": {
                    "loop": True,
                    "frames": [
                        {"duration_ms": 260, "transforms": {"rune": {"color_swap": {"a": "a"}}}},
                        {"duration_ms": 260, "transforms": {"rune": {"color_swap": {"a": "b"}}}},
                    ],
                }
            },
            "export": {},
            "validation": {},
        }
    )
    frames = resolve_frames(doc)
    export = build_animation_player_export(doc, frames)

    assert export.tracks == []  # no position/visible change -> no keyframe track
    assert set(export.animations) == {"idle"}
    assert export.animations["idle"].total_duration_ms == 520
    assert export.animations["idle"].loop is True


# --- procedural passthrough ----------------------------------------------------------------------


def test_procedural_entries_carried_through() -> None:
    doc = _prop_doc()
    sheet, frames = _prop_sheet(doc)
    manifest = build_godot_manifest(
        doc, sheet=sheet, texture_paths={"atlas": "beacon/atlas.png"}, spec_hash="x", frames=frames
    )
    assert manifest.procedural["spin"].shader == "glow_pulse"
    assert manifest.procedural["spin"].params == {"speed": 1.5}
    assert manifest.procedural["spin"].target_region == "vane"


# --- kind mismatches -----------------------------------------------------------------------------


def test_terrain_doc_with_sprite_frames_request_raises() -> None:
    doc = _terrain_doc()
    prop_doc = _prop_doc()
    sheet, frames = _prop_sheet(prop_doc)
    with pytest.raises(ExportError, match="sprite_frames"):
        build_godot_manifest(
            doc,
            sheet=sheet,
            texture_paths={"atlas": "forest/atlas.png"},
            spec_hash="x",
            frames=frames,
        )


def test_sprite_doc_with_tileset_request_raises() -> None:
    doc = _prop_doc()
    cells = _terrain_atlas(_terrain_doc())
    with pytest.raises(ExportError, match="tileset"):
        build_godot_manifest(
            doc, texture_paths={"atlas": "beacon/atlas.png"}, spec_hash="x", atlas_cells=cells
        )


# --- texture path normalisation -------------------------------------------------------------------


def test_absolute_texture_path_raises() -> None:
    doc = _prop_doc()
    sheet, frames = _prop_sheet(doc)
    with pytest.raises(ExportError):
        build_godot_manifest(
            doc, sheet=sheet, texture_paths={"atlas": "/etc/passwd"}, spec_hash="x", frames=frames
        )


def test_escaping_texture_path_raises() -> None:
    doc = _prop_doc()
    sheet, frames = _prop_sheet(doc)
    with pytest.raises(ExportError):
        build_godot_manifest(
            doc,
            sheet=sheet,
            texture_paths={"atlas": "../../outside/atlas.png"},
            spec_hash="x",
            frames=frames,
        )


def test_windows_backslash_texture_path_is_normalised() -> None:
    doc = _prop_doc()
    sheet, frames = _prop_sheet(doc)
    manifest = build_godot_manifest(
        doc,
        sheet=sheet,
        texture_paths={"atlas": "assets\\beacon\\atlas.png"},
        spec_hash="x",
        frames=frames,
    )
    assert manifest.textures["atlas"] == "assets/beacon/atlas.png"


# --- spec_hash --------------------------------------------------------------------------------


def test_spec_hash_round_trips_into_json(tmp_path: Path) -> None:
    doc = _prop_doc()
    sheet, frames = _prop_sheet(doc)
    manifest = build_godot_manifest(
        doc,
        sheet=sheet,
        texture_paths={"atlas": "beacon/atlas.png"},
        spec_hash="deadbeef123",
        frames=frames,
    )
    assert manifest.spec_hash == "deadbeef123"

    path = write_godot_manifest(manifest, tmp_path / "out")
    assert json.loads(path.read_text())["spec_hash"] == "deadbeef123"


# --- write_godot_manifest determinism + golden ------------------------------------------------


def test_write_godot_manifest_byte_identical_and_matches_golden(tmp_path: Path) -> None:
    doc = _prop_doc()
    sheet, frames = _prop_sheet(doc)
    manifest = build_godot_manifest(
        doc, sheet=sheet, texture_paths={"atlas": "beacon/atlas.png"}, spec_hash="x", frames=frames
    )

    path1 = write_godot_manifest(manifest, tmp_path / "run1")
    path2 = write_godot_manifest(manifest, tmp_path / "run2")
    assert path1.read_bytes() == path2.read_bytes()

    golden = (FIXTURES_DIR / "beacon.forge.json").read_bytes()
    assert path1.read_bytes() == golden


def test_write_godot_manifest_terrain_matches_golden(tmp_path: Path) -> None:
    doc = _terrain_doc()
    cells = _terrain_atlas(doc)
    manifest = build_godot_manifest(
        doc, texture_paths={"atlas": "forest/atlas.png"}, spec_hash="x", atlas_cells=cells
    )
    path = write_godot_manifest(manifest, tmp_path / "out")
    golden = (FIXTURES_DIR / "forest.forge.json").read_bytes()
    assert path.read_bytes() == golden


def test_write_godot_manifest_creates_out_dir(tmp_path: Path) -> None:
    doc = _prop_doc()
    sheet, frames = _prop_sheet(doc)
    manifest = build_godot_manifest(
        doc, sheet=sheet, texture_paths={"atlas": "beacon/atlas.png"}, spec_hash="x", frames=frames
    )
    out_dir = tmp_path / "nested" / "dir"
    path = write_godot_manifest(manifest, out_dir)
    assert path.exists()
    assert json.loads(path.read_text())["asset_id"] == "beacon"
