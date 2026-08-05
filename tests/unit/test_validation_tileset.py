from __future__ import annotations

from typing import Any

from pixel_forge.domain.palette import resolve_palette
from pixel_forge.rendering.canvas import RGBA, Canvas
from pixel_forge.schemas import TerrainAsset, parse_asset_doc
from pixel_forge.validation.engine import RuleContext, run_validation

RED: RGBA = (255, 0, 0, 255)
BLUE: RGBA = (0, 0, 255, 255)
TRANSPARENT: RGBA = (0, 0, 0, 0)


def _terrain_doc(
    *,
    tiles: dict[str, Any] | None = None,
    terrain_sets: dict[str, Any] | None = None,
    transitions: list[dict[str, Any]] | None = None,
    animated_tiles: dict[str, Any] | None = None,
    sample_map: dict[str, Any] | None = None,
    validation: dict[str, Any] | None = None,
) -> TerrainAsset:
    data: dict[str, Any] = {
        "schema_version": 1,
        "asset": {"id": "forest", "type": "terrain", "canvas": [16, 16]},
        "palette": {"id": "p", "colors": [{"id": "red", "hex": "#ff0000"}]},
        "export": {},
        "validation": validation or {},
        "tiles": tiles if tiles is not None else {},
        "terrain_sets": terrain_sets or {},
        "transitions": transitions or [],
        "animated_tiles": animated_tiles or {},
    }
    if sample_map is not None:
        data["sample_map"] = sample_map
    doc = parse_asset_doc(data)
    assert isinstance(doc, TerrainAsset)
    return doc


def _solid(w: int, h: int, rgba: RGBA) -> Canvas:
    c = Canvas(w, h)
    c.draw_rect((0, 0), (w, h), rgba, fill=True)
    return c


def _ctx(doc: TerrainAsset, tiles: dict[str, Canvas]) -> RuleContext:
    return RuleContext(
        doc=doc, palette=resolve_palette(doc.palette), frames={}, resolved=[], tiles=tiles
    )


def _tile(size: tuple[int, int] = (4, 4), terrain: str | None = None) -> dict[str, Any]:
    return {"size": list(size), "regions": {}, "anchors": {}, "terrain": terrain}


# ---- TIL001: transition tile ids / terrain-pair coverage --------------------------


def test_til001_fires_on_unknown_transition_tile_id() -> None:
    doc = _terrain_doc(
        tiles={"grass_a": _tile(terrain="grass"), "dirt_a": _tile(terrain="dirt")},
        transitions=[
            {"from_terrain": "grass", "to_terrain": "dirt", "tile_id": "missing_tile", "mask": "N"}
        ],
    )
    ctx = _ctx(doc, {})
    report = run_validation(ctx, only=["TIL001"])
    assert len(report.findings) == 1
    assert report.findings[0].rule_id == "TIL001"
    assert report.findings[0].severity == "error"


def test_til001_does_not_fire_when_transition_covers_pair() -> None:
    doc = _terrain_doc(
        tiles={"grass_dirt_a": _tile()},
        transitions=[
            {
                "from_terrain": "grass",
                "to_terrain": "dirt",
                "tile_id": "grass_dirt_a",
                "mask": "N",
            }
        ],
    )
    ctx = _ctx(doc, {})
    report = run_validation(ctx, only=["TIL001"])
    assert report.findings == []


# ---- TIL002: terrain-set adjacency tile ids ----------------------------------------


def test_til002_fires_on_unknown_adjacency_tile_id() -> None:
    doc = _terrain_doc(
        tiles={"grass_a": _tile()},
        terrain_sets={"grass_set": {"mode": "corners_and_edges", "tiles": ["missing_id"]}},
    )
    ctx = _ctx(doc, {})
    report = run_validation(ctx, only=["TIL002"])
    assert len(report.findings) == 1
    assert report.findings[0].rule_id == "TIL002"
    assert report.findings[0].severity == "error"


def test_til002_does_not_fire_when_adjacency_tiles_known() -> None:
    doc = _terrain_doc(
        tiles={"grass_a": _tile()},
        terrain_sets={"grass_set": {"mode": "corners_and_edges", "tiles": ["grass_a"]}},
    )
    ctx = _ctx(doc, {})
    report = run_validation(ctx, only=["TIL002"])
    assert report.findings == []


# ---- TIL003: visible seams ----------------------------------------------------------


def test_til003_fires_on_self_seam_mismatch() -> None:
    doc = _terrain_doc(tiles={"a": _tile()})
    canvas = Canvas(4, 4)
    canvas.draw_rect((0, 0), (4, 1), RED, fill=True)  # top row red, rest transparent
    ctx = _ctx(doc, {"a": canvas})
    report = run_validation(ctx, only=["TIL003"])
    assert len(report.findings) >= 1
    assert all(f.rule_id == "TIL003" for f in report.findings)
    assert any(f.severity == "error" for f in report.findings)


def test_til003_does_not_fire_on_uniform_self_tiling_tile() -> None:
    doc = _terrain_doc(tiles={"a": _tile()})
    ctx = _ctx(doc, {"a": _solid(4, 4, RED)})
    report = run_validation(ctx, only=["TIL003"])
    assert report.findings == []


# ---- TIL004: animated seam error -----------------------------------------------------


def test_til004_fires_on_differing_animated_frame_sizes() -> None:
    doc = _terrain_doc(
        tiles={"w0": _tile(size=(4, 4)), "w1": _tile(size=(6, 6))},
        animated_tiles={"water": {"frames": ["w0", "w1"], "frame_duration_ms": 100}},
    )
    ctx = _ctx(doc, {"w0": _solid(4, 4, BLUE), "w1": _solid(6, 6, BLUE)})
    report = run_validation(ctx, only=["TIL004"])
    assert len(report.findings) == 1
    assert report.findings[0].rule_id == "TIL004"
    assert report.findings[0].severity == "error"


def test_til004_does_not_fire_when_frames_match_and_self_tile() -> None:
    doc = _terrain_doc(
        tiles={"w0": _tile(size=(4, 4)), "w1": _tile(size=(4, 4))},
        animated_tiles={"water": {"frames": ["w0", "w1"], "frame_duration_ms": 100}},
    )
    ctx = _ctx(doc, {"w0": _solid(4, 4, BLUE), "w1": _solid(4, 4, BLUE)})
    report = run_validation(ctx, only=["TIL004"])
    assert report.findings == []


# ---- TIL005: sample_map atlas coordinates --------------------------------------------


def test_til005_fires_on_unknown_sample_map_tile_id() -> None:
    doc = _terrain_doc(
        tiles={"a": _tile()},
        sample_map={"size": [2, 2], "layers": {"ground": [["a", "a"], ["a", "missing"]]}},
    )
    ctx = _ctx(doc, {})
    report = run_validation(ctx, only=["TIL005"])
    assert len(report.findings) == 1
    assert report.findings[0].rule_id == "TIL005"
    assert report.findings[0].severity == "error"


def test_til005_does_not_fire_on_valid_sample_map() -> None:
    doc = _terrain_doc(
        tiles={"a": _tile()},
        sample_map={"size": [2, 2], "layers": {"ground": [["a", "a"], ["a", "a"]]}},
    )
    ctx = _ctx(doc, {})
    report = run_validation(ctx, only=["TIL005"])
    assert report.findings == []


# ---- TIL006: collision metadata mismatch ---------------------------------------------


def test_til006_fires_on_differing_collision_values() -> None:
    doc = _terrain_doc(
        tiles={
            "a": {"size": [4, 4], "regions": {}, "anchors": {}, "collision": "solid"},
            "b": {"size": [4, 4], "regions": {}, "anchors": {}, "collision": "none"},
        },
        terrain_sets={"grass_set": {"mode": "corners_and_edges", "tiles": ["a", "b"]}},
    )
    ctx = _ctx(doc, {})
    report = run_validation(ctx, only=["TIL006"])
    assert len(report.findings) == 1
    assert report.findings[0].rule_id == "TIL006"
    assert report.findings[0].severity == "warning"


def test_til006_does_not_fire_when_collision_agrees() -> None:
    doc = _terrain_doc(
        tiles={
            "a": {"size": [4, 4], "regions": {}, "anchors": {}, "collision": "solid"},
            "b": {"size": [4, 4], "regions": {}, "anchors": {}, "collision": "solid"},
        },
        terrain_sets={"grass_set": {"mode": "corners_and_edges", "tiles": ["a", "b"]}},
    )
    ctx = _ctx(doc, {})
    report = run_validation(ctx, only=["TIL006"])
    assert report.findings == []


# ---- TIL007: excessive repeated patterns (heuristic) ----------------------------------


def test_til007_fires_on_excessive_repetition() -> None:
    doc = _terrain_doc(
        tiles={"a": _tile(), "b": _tile()},
        sample_map={"size": [4, 1], "layers": {"ground": [["a", "a", "a", "b"]]}},  # 75% "a"
    )
    ctx = _ctx(doc, {})
    report = run_validation(ctx, only=["TIL007"])
    assert len(report.findings) == 1
    assert report.findings[0].rule_id == "TIL007"
    assert report.findings[0].severity == "warning"


def test_til007_does_not_fire_within_repeat_ratio() -> None:
    doc = _terrain_doc(
        tiles={"a": _tile(), "b": _tile()},
        sample_map={"size": [4, 1], "layers": {"ground": [["a", "a", "b", "b"]]}},  # 50% "a"
    )
    ctx = _ctx(doc, {})
    report = run_validation(ctx, only=["TIL007"])
    assert report.findings == []
