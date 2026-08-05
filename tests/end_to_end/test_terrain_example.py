"""End-to-end proof that examples/assets/forest_tileset/forest_tileset.yaml renders and
validates clean, every tile is seamless against itself, the atlas packs
deterministically, and the sample map/transitions are internally consistent."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from pixel_forge import api
from pixel_forge.animation.resolver import resolve_terrain_frames
from pixel_forge.domain.loader import load_asset_doc
from pixel_forge.domain.palette import resolve_palette
from pixel_forge.rendering.local import render_terrain_tiles
from pixel_forge.rendering.sheet import build_atlas, check_seams
from pixel_forge.schemas import GodotManifest, TerrainAsset
from pixel_forge.validation.engine import RuleContext, run_validation

EXAMPLES_ROOT = Path(__file__).resolve().parents[2] / "examples"
ASSET_PATH = EXAMPLES_ROOT / "assets" / "forest_tileset" / "forest_tileset.yaml"


def _load() -> TerrainAsset:
    doc = load_asset_doc(ASSET_PATH)
    assert isinstance(doc, TerrainAsset)
    return doc


def test_load_render_and_resolve_terrain_frames() -> None:
    doc = _load()
    tiles = render_terrain_tiles(doc)
    assert set(tiles) == set(doc.tiles)

    resolved = resolve_terrain_frames(doc)
    static_ids = {f.tile_id for f in resolved if f.animated_tile is None}
    assert static_ids == set(doc.tiles)
    animated = [f for f in resolved if f.animated_tile == "water"]
    assert [f.tile_id for f in animated] == ["water_1", "water_2", "water_3"]


def test_every_tile_is_16x16() -> None:
    doc = _load()
    tiles = render_terrain_tiles(doc)
    for tile_id, canvas in tiles.items():
        assert (canvas.width, canvas.height) == (16, 16), tile_id


def test_every_tile_self_tiles_with_zero_seam_mismatch() -> None:
    doc = _load()
    tiles = render_terrain_tiles(doc)
    results = check_seams(tiles)
    self_pairs = [r for r in results if r.tile_a == r.tile_b]
    assert self_pairs, "expected at least one self-pair result"
    offenders = [r for r in self_pairs if r.mismatched_pixels != 0]
    assert not offenders, [
        f"{r.tile_a} edge {r.edge}: {r.mismatched_pixels}px mismatch" for r in offenders
    ]


def test_atlas_packs_deterministically() -> None:
    doc = _load()
    tiles = render_terrain_tiles(doc)
    atlas_a, cells_a = build_atlas(tiles)
    atlas_b, cells_b = build_atlas(tiles)
    assert atlas_a.equals(atlas_b)
    assert cells_a == cells_b
    # sorted-key packing: every tile gets a unique, stable (x, y)
    assert len({(c.x, c.y) for c in cells_a.values()}) == len(cells_a)


def test_sample_map_references_only_real_tiles_and_matches_size() -> None:
    doc = _load()
    assert doc.sample_map is not None
    width, height = doc.sample_map.size
    for layer_name, rows in doc.sample_map.layers.items():
        assert len(rows) == height, layer_name
        for row in rows:
            assert len(row) == width, layer_name
            for tile_id in row:
                assert tile_id in doc.tiles, tile_id


def test_every_transition_mask_has_a_real_tile() -> None:
    doc = _load()
    expected_masks = {"N", "NE", "E", "SE", "S", "SW", "W", "NW"}
    seen_masks = {t.mask for t in doc.transitions}
    assert expected_masks <= seen_masks
    for transition in doc.transitions:
        assert transition.tile_id in doc.tiles, transition.tile_id


def test_validation_report_has_no_blocking_findings() -> None:
    doc = _load()
    tiles = render_terrain_tiles(doc)
    palette = resolve_palette(doc.palette)
    ctx = RuleContext(doc=doc, palette=palette, frames={}, resolved=(), tiles=tiles)
    report = run_validation(ctx)
    if report.blocking:
        print(report.to_text())
    assert report.blocking is False


def test_render_is_deterministic() -> None:
    doc = _load()
    tiles_a = render_terrain_tiles(doc)
    tiles_b = render_terrain_tiles(doc)
    assert tiles_a.keys() == tiles_b.keys()
    for tile_id in tiles_a:
        assert tiles_a[tile_id].equals(tiles_b[tile_id])


# --- Godot animated-tile contract: the built forest_tileset.forge.json ------------------


@pytest.fixture
def _built_forge_json(tmp_path: Path) -> dict[str, Any]:
    """Builds `examples/` (copied so the repo tree is never written to) and returns the
    parsed `build/godot/forest_tileset.forge.json` the real `build_all` pipeline wrote."""
    root = tmp_path / "examples"
    shutil.copytree(EXAMPLES_ROOT, root, ignore=shutil.ignore_patterns("build"))
    report = api.build_all(root)
    assert report.blocking is False, report.failed

    manifest_path = root / "build" / "godot" / "forest_tileset.forge.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    GodotManifest.model_validate(data)  # round-trips through the schema
    return data


def test_animated_tile_frames_are_contiguous_and_horizontally_adjacent(
    _built_forge_json: dict[str, Any],
) -> None:
    tileset = _built_forge_json["tileset"]
    animated_tiles = tileset["animated_tiles"]
    assert animated_tiles, "forest_tileset.yaml defines an animated water tile"

    for name, anim in animated_tiles.items():
        frames = anim["frames"]
        assert len(frames) >= 2, name
        base_x, base_y = frames[0]["x"], frames[0]["y"]
        for i, frame in enumerate(frames):
            assert frame["x"] == base_x + i, f"{name} frame {i} not horizontally adjacent"
            assert frame["y"] == base_y, f"{name} frame {i} not on the base frame's row"


def test_animated_frames_do_not_appear_as_separate_tiles(
    _built_forge_json: dict[str, Any],
) -> None:
    tileset = _built_forge_json["tileset"]
    tile_ids = {t["tile_id"] for t in tileset["tiles"]}

    for anim in tileset["animated_tiles"].values():
        frame_ids = [f["tile_id"] for f in anim["frames"]]
        base_id, extra_ids = frame_ids[0], frame_ids[1:]
        assert base_id in tile_ids
        for frame_id in extra_ids:
            assert frame_id not in tile_ids, f"{frame_id} must not be its own tiles entry"
