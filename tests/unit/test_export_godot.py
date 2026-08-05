"""Tests for the neutral Godot import manifest exporter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pixel_forge.animation.resolver import resolve_frames
from pixel_forge.errors import ExportError
from pixel_forge.exporters.godot import (
    build_animation_player,
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
            },
            "terrain_sets": {"ground": {"mode": "corners_and_edges", "tiles": ["grass", "dirt"]}},
            "transitions": [
                {"from_terrain": "grass", "to_terrain": "dirt", "tile_id": "dirt", "mask": mask}
                for mask in ("N", "NE", "E", "SW")
            ],
            "animated_tiles": {
                "water_flow": {"frames": ["grass", "dirt"], "frame_duration_ms": 150, "loop": True}
            },
            "sample_map": {
                "size": [2, 2],
                "layers": {"ground": [["grass", "dirt"], ["dirt", "grass"]]},
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

    coords = {t.tile_id: (t.x, t.y) for t in tileset.tiles}
    assert coords == {"dirt": (0, 0), "grass": (1, 0)}
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
    assert [(f.tile_id, f.x, f.y) for f in water.frames] == [("grass", 1, 0), ("dirt", 0, 0)]
    assert water.frame_duration_ms == 150
    assert water.loop is True


def test_animated_tile_unknown_frame_tile_raises() -> None:
    doc = _terrain_doc()
    cells = {"grass": _terrain_atlas(doc)["grass"]}  # drop "dirt" -> unknown frame reference
    with pytest.raises(ExportError):
        build_tileset(doc, cells, "forest/atlas.png")


# --- sample map -----------------------------------------------------------------------------------


def test_sample_map_coords_resolved() -> None:
    doc = _terrain_doc()
    cells = _terrain_atlas(doc)
    tileset = build_tileset(doc, cells, "forest/atlas.png")

    assert tileset.sample_map is not None
    assert tileset.sample_map.size == (2, 2)
    assert tileset.sample_map.layers["ground"] == [[(1, 0), (0, 0)], [(0, 0), (1, 0)]]


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
