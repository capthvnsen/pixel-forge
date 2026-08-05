from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from pixel_forge.domain.geometry import (
    Rect,
    anchor_world_pos,
    bbox_of_points,
    mirror_anchors,
    mirror_point_x,
    silhouette_area,
    silhouette_centroid,
)
from pixel_forge.domain.hashing import content_hash, file_hash, short
from pixel_forge.domain.loader import (
    append_jsonl,
    dump_asset_doc,
    dump_yaml,
    load_asset_doc,
    load_jsonl,
    load_yaml,
)
from pixel_forge.domain.palette import (
    check_palette_limit,
    hex_to_rgba,
    resolve_palette,
    rgba_to_hex,
)
from pixel_forge.domain.paths import ProjectPaths, safe_join, validate_asset_id
from pixel_forge.domain.project import Project
from pixel_forge.errors import ForgeError, PaletteError, PathSecurityError, SchemaError
from pixel_forge.schemas.asset import parse_asset_doc
from pixel_forge.schemas.palette import Palette, PaletteColor
from pixel_forge.schemas.project import ProjectConfig


def _character_doc() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "asset": {
            "id": "engineer",
            "type": "character",
            "canvas": [64, 64],
            "baseline_y": 60,
        },
        "palette": {
            "id": "engineer_palette",
            "colors": [
                {"id": "skin", "hex": "#e8b58c"},
                {"id": "outline", "hex": "#101010"},
            ],
        },
        "directions": ["south", "north"],
        "mirror": {},
        "anchors": {"root": [0, 0]},
        "regions": {
            "body": {
                "anchor": "root",
                "layer": 0,
                "shapes": [
                    {"op": "pixel", "color": "skin", "at": [0, 0]},
                    {"op": "rect", "color": "outline", "at": [1, 1], "size": [2, 2]},
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


# --- safe_join: path-traversal attack suite ---------------------------------


def test_safe_join_rejects_dotdot_traversal(tmp_path: Path) -> None:
    with pytest.raises(PathSecurityError):
        safe_join(tmp_path, "../etc/passwd")


def test_safe_join_rejects_absolute_component(tmp_path: Path) -> None:
    with pytest.raises(PathSecurityError):
        safe_join(tmp_path, "/etc/passwd")


def test_safe_join_rejects_dotdot_within_a_single_part(tmp_path: Path) -> None:
    with pytest.raises(PathSecurityError):
        safe_join(tmp_path, "a/../../b")


def test_safe_join_rejects_nul_byte(tmp_path: Path) -> None:
    with pytest.raises(PathSecurityError):
        safe_join(tmp_path, "evil\x00.yaml")


def test_safe_join_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "evil_link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(PathSecurityError):
        safe_join(root, "evil_link", "newfile.yaml")


def test_safe_join_rejects_symlink_escape_to_nonexistent_leaf(tmp_path: Path) -> None:
    """Even when the leaf file doesn't exist yet, an escaping symlink ancestor is caught."""
    root = tmp_path / "root"
    root.mkdir()
    (root / "evil_link").symlink_to(Path("/tmp"), target_is_directory=True)

    with pytest.raises(PathSecurityError):
        safe_join(root, "evil_link", "does_not_exist_yet.yaml")


def test_safe_join_legitimate_nested_join_succeeds(tmp_path: Path) -> None:
    result = safe_join(tmp_path, "assets", "engineer", "engineer.yaml")
    assert result == (tmp_path / "assets" / "engineer" / "engineer.yaml").resolve()


def test_validate_asset_id_rejects_traversal() -> None:
    with pytest.raises(PathSecurityError):
        validate_asset_id("../evil")


def test_validate_asset_id_rejects_nul_byte() -> None:
    with pytest.raises(PathSecurityError):
        validate_asset_id("evil\x00")


def test_validate_asset_id_rejects_bad_chars_and_length() -> None:
    with pytest.raises(PathSecurityError):
        validate_asset_id("Engineer")  # uppercase not allowed
    with pytest.raises(PathSecurityError):
        validate_asset_id("_leading_underscore")
    with pytest.raises(PathSecurityError):
        validate_asset_id("a" * 65)


def test_validate_asset_id_accepts_legit_id() -> None:
    assert validate_asset_id("engineer_01") == "engineer_01"


def test_project_paths_methods_reject_hostile_asset_id(tmp_path: Path) -> None:
    config = ProjectConfig(name="Test")
    paths = ProjectPaths(root=tmp_path, config=config)
    with pytest.raises(PathSecurityError):
        paths.asset_spec("../../etc")
    with pytest.raises(PathSecurityError):
        paths.asset_dir("../../etc")
    with pytest.raises(PathSecurityError):
        paths.asset_revisions("../../etc")
    with pytest.raises(PathSecurityError):
        paths.build_asset_dir("../../etc")


def test_project_paths_layout(tmp_path: Path) -> None:
    config = ProjectConfig(name="Test")
    paths = ProjectPaths(root=tmp_path, config=config)
    assert paths.config_file == (tmp_path / "pixel-forge.yaml").resolve()
    assert paths.assets_dir == (tmp_path / "assets").resolve()
    assert paths.build_dir == (tmp_path / "build").resolve()
    assert paths.references_dir == (tmp_path / "references").resolve()
    assert (
        paths.asset_spec("engineer")
        == (tmp_path / "assets" / "engineer" / "engineer.yaml").resolve()
    )
    assert (
        paths.asset_revisions("engineer")
        == (tmp_path / "assets" / "engineer" / "revisions.jsonl").resolve()
    )
    assert paths.build_asset_dir("engineer") == (tmp_path / "build" / "engineer").resolve()
    assert paths.build_godot_dir() == (tmp_path / "build" / "godot").resolve()


# --- loader -------------------------------------------------------------


def test_yaml_dump_preserves_key_order_and_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "doc.yaml"
    data = {"zeta": 1, "alpha": 2, "middle": {"b": 1, "a": 2}}
    dump_yaml(data, path)
    text = path.read_text()
    assert text.index("zeta") < text.index("alpha")
    assert load_yaml(path) == data


def test_load_yaml_raises_schema_error_on_malformed_yaml(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("key: [1, 2\nother: value")
    with pytest.raises(SchemaError, match="line"):
        load_yaml(path)


def test_load_yaml_raises_schema_error_on_non_mapping_document(tmp_path: Path) -> None:
    path = tmp_path / "list.yaml"
    path.write_text("- 1\n- 2\n")
    with pytest.raises(SchemaError):
        load_yaml(path)


def test_load_asset_doc_raises_schema_error_naming_bad_field(tmp_path: Path) -> None:
    data = _character_doc()
    data["asset"]["canvas"] = "not-a-vec2"
    path = tmp_path / "bad_asset.yaml"
    dump_yaml(data, path)
    with pytest.raises(SchemaError, match=r"asset\.canvas"):
        load_asset_doc(path)


def test_load_asset_doc_names_nested_shape_field(tmp_path: Path) -> None:
    data = _character_doc()
    data["regions"]["body"]["shapes"] = [{"op": "triangle", "color": "skin", "at": [0, 0]}]
    path = tmp_path / "bad_shape.yaml"
    dump_yaml(data, path)
    with pytest.raises(SchemaError, match=r"regions\.body\.shapes\.0"):
        load_asset_doc(path)


def test_asset_doc_round_trip_load_dump_load_equal(tmp_path: Path) -> None:
    doc = parse_asset_doc(_character_doc())
    path = tmp_path / "engineer.yaml"
    dump_asset_doc(doc, path)
    assert "kind:" not in path.read_text()
    reloaded = load_asset_doc(path)
    assert reloaded == doc

    # dumping again after reloading must be byte-identical (round-trip stability).
    path2 = tmp_path / "engineer2.yaml"
    dump_asset_doc(reloaded, path2)
    assert path.read_text() == path2.read_text()


def test_jsonl_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "revisions.jsonl"
    assert load_jsonl(path) == []
    append_jsonl(path, {"b": 1, "a": 2})
    append_jsonl(path, {"c": 3})
    records = load_jsonl(path)
    assert records == [{"a": 2, "b": 1}, {"c": 3}]
    text = path.read_text()
    assert text.splitlines()[0] == '{"a": 2, "b": 1}'


# --- palette --------------------------------------------------------------


def _palette() -> Palette:
    return Palette(
        id="p",
        colors=[
            PaletteColor(id="red", hex="#ff0000"),
            PaletteColor(id="red2", hex="#fe0101"),
            PaletteColor(id="blue", hex="#0000ff"),
        ],
    )


def test_hex_to_rgba_and_back() -> None:
    assert hex_to_rgba("#ff0000") == (255, 0, 0, 255)
    assert hex_to_rgba("#ff000080") == (255, 0, 0, 128)
    assert rgba_to_hex((255, 0, 0, 255)) == "#ff0000"
    assert rgba_to_hex((255, 0, 0, 128)) == "#ff000080"


def test_resolved_palette_rgba_unknown_id_lists_valid_ids() -> None:
    resolved = resolve_palette(_palette())
    assert resolved.rgba("red") == (255, 0, 0, 255)
    assert resolved.ids == ("red", "red2", "blue")
    assert resolved.size == 3
    with pytest.raises(PaletteError, match="red, red2, blue"):
        resolved.rgba("missing")


def test_resolved_palette_contains_rgba() -> None:
    resolved = resolve_palette(_palette())
    assert resolved.contains_rgba((0, 0, 255, 255))
    assert not resolved.contains_rgba((1, 2, 3, 255))


def test_resolved_palette_nearest() -> None:
    resolved = resolve_palette(_palette())
    assert resolved.nearest((255, 0, 0, 255)) == "red"
    assert resolved.nearest((0, 0, 250, 255)) == "blue"


def test_resolved_palette_nearest_breaks_ties_by_earlier_declared_id() -> None:
    tie_palette = Palette(
        id="tie",
        colors=[
            PaletteColor(id="a", hex="#ff0000"),
            PaletteColor(id="b", hex="#0000ff"),
        ],
    )
    resolved = resolve_palette(tie_palette)
    # (128, 0, 128) is exactly equidistant from "a" (255,0,0) and "b" (0,0,255):
    # 127**2 + 128**2 == 128**2 + 127**2. The earlier-declared id must win.
    assert resolved.nearest((128, 0, 128, 255)) == "a"


def test_check_palette_limit() -> None:
    palette = _palette()
    assert check_palette_limit(palette, 5) == []
    assert check_palette_limit(palette, 2) == ["blue"]
    assert check_palette_limit(palette, 0) == ["red", "red2", "blue"]


# --- geometry ---------------------------------------------------------------


def test_rect_properties_and_union() -> None:
    a = Rect(0, 0, 10, 10)
    b = Rect(5, 5, 10, 10)
    assert a.right == 10
    assert a.bottom == 10
    assert not a.is_empty
    assert Rect(0, 0, 0, 5).is_empty
    union = a.union(b)
    assert union == Rect(0, 0, 15, 15)
    assert a.intersects(b)
    assert not a.intersects(Rect(20, 20, 5, 5))
    assert a.contains_point((0, 0))
    assert not a.contains_point((10, 10))
    assert a.translated(5, 5) == Rect(5, 5, 10, 10)


def test_anchor_world_pos() -> None:
    anchors = {"root": (10, 20)}
    assert anchor_world_pos(anchors, "root") == (10, 20)
    assert anchor_world_pos(anchors, "root", (1, -2)) == (11, 18)
    with pytest.raises(ForgeError, match="missing"):
        anchor_world_pos(anchors, "missing")


def test_mirror_point_x_convention() -> None:
    assert mirror_point_x((0, 5), 64) == (63, 5)
    assert mirror_point_x((63, 5), 64) == (0, 5)
    assert mirror_point_x((32, 5), 64) == (31, 5)


def test_mirror_anchors() -> None:
    anchors = {"root": (0, 0), "hand": (10, 20)}
    mirrored = mirror_anchors(anchors, 64)
    assert mirrored == {"root": (63, 0), "hand": (53, 20)}


def test_bbox_of_points() -> None:
    assert bbox_of_points([]) is None
    assert bbox_of_points([(1, 1), (3, 5), (2, 0)]) == Rect(1, 0, 3, 6)


def test_silhouette_area_and_centroid() -> None:
    mask = np.zeros((4, 4), dtype=bool)
    mask[1, 1] = True
    mask[1, 2] = True
    assert silhouette_area(mask) == 2
    assert silhouette_centroid(mask) == (1.5, 1.0)
    assert silhouette_centroid(np.zeros((4, 4), dtype=bool)) is None


# --- hashing ----------------------------------------------------------------


def test_content_hash_stable_across_calls() -> None:
    obj = {"b": 1, "a": [1, 2, 3], "nested": {"z": 1, "y": 2}}
    assert content_hash(obj) == content_hash(obj)
    assert content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1})


def test_content_hash_sensitive_to_one_field_change() -> None:
    base = {"a": 1, "b": 2}
    changed = {"a": 1, "b": 3}
    assert content_hash(base) != content_hash(changed)


def test_content_hash_accepts_pydantic_model() -> None:
    doc = parse_asset_doc(_character_doc())
    h1 = content_hash(doc)
    h2 = content_hash(doc)
    assert h1 == h2
    assert len(h1) == 64


def test_file_hash_and_short(tmp_path: Path) -> None:
    path = tmp_path / "f.bin"
    path.write_bytes(b"hello world")
    h = file_hash(path)
    assert len(h) == 64
    assert short(h) == h[:12]
    assert short(h, 6) == h[:6]


# --- project ------------------------------------------------------------


def test_project_create_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    project1 = Project.create(root, "Demo")
    project2 = Project.create(root, "Demo")
    assert project1.config == project2.config
    assert (root / "assets").is_dir()
    assert (root / "build").is_dir()
    assert (root / "references").is_dir()


def test_project_create_raises_on_conflicting_config(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    Project.create(root, "Demo")
    with pytest.raises(ForgeError):
        Project.create(root, "Different Name")


def test_project_load_missing_config_raises_schema_error(tmp_path: Path) -> None:
    with pytest.raises(SchemaError, match="pixel-forge init"):
        Project.load(tmp_path / "nonexistent")


def test_project_load_round_trips_create(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    Project.create(root, "Demo")
    loaded = Project.load(root)
    assert loaded.config.name == "Demo"


def test_discover_assets_ordering(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    project = Project.create(root, "Demo")
    for asset_id in ("zeta", "alpha", "middle"):
        asset_dir = project.paths.asset_dir(asset_id)
        asset_dir.mkdir(parents=True)
        (asset_dir / f"{asset_id}.yaml").write_text("placeholder: true\n")
    # A directory without a matching spec file must not be discovered.
    (project.paths.assets_dir / "no_spec").mkdir()

    assert project.discover_assets() == ["alpha", "middle", "zeta"]


def test_project_save_and_load_asset_round_trip(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    project = Project.create(root, "Demo")
    doc = parse_asset_doc(_character_doc())
    saved_path = project.save_asset(doc)
    assert saved_path == project.paths.asset_spec("engineer")
    reloaded = project.load_asset("engineer")
    assert reloaded == doc
    assert project.discover_assets() == ["engineer"]
