"""Integration tests for the `api` service layer — the only surface the CLI and MCP
server call. Exercises the full pipeline (init -> new_asset -> render -> validate ->
preview -> export -> revise -> build) purely through `pixel_forge.api`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from pixel_forge import api, templates
from pixel_forge.domain import Project
from pixel_forge.errors import AssetNotFoundError, ExportError, ForgeError, PathSecurityError
from pixel_forge.schemas import AssetType, GodotManifest, OperationSpec, parse_asset_doc

ASSET_TYPES: tuple[AssetType, ...] = ("character", "enemy", "prop", "terrain")


def _init(tmp_path: Path, name: str = "demo") -> Path:
    root = tmp_path / name
    api.init_project(root, name)
    return root


def _make_broken_asset(root: Path, asset_id: str) -> None:
    """A structurally valid spec whose shape references a palette colour that does
    not exist — parses fine, but rendering raises `PaletteError` (a `ForgeError`)."""
    data = templates.asset_template("character", asset_id)
    data["regions"]["block"]["shapes"][0]["color"] = "not_a_real_color"
    Project.load(root).save_asset(parse_asset_doc(data))


def _make_two_direction_asset(root: Path, asset_id: str) -> None:
    """The starter character template with a second direction added, for exercising
    per-direction preview fanout."""
    data = templates.asset_template("character", asset_id)
    data["directions"] = ["south", "north"]
    Project.load(root).save_asset(parse_asset_doc(data))


# --- init_project --------------------------------------------------------------------------


def test_init_project_creates_layout(tmp_path: Path) -> None:
    root = tmp_path / "demo"
    config = api.init_project(root, "demo")
    assert config.name == "demo"
    assert (root / "pixel-forge.yaml").is_file()
    assert (root / "assets").is_dir()
    assert (root / "build").is_dir()
    assert (root / "references").is_dir()


def test_init_project_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "demo"
    first = api.init_project(root, "demo")
    second = api.init_project(root, "demo")
    assert first == second


def test_init_project_dry_run_writes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "demo"
    config = api.init_project(root, "demo", dry_run=True)
    assert config.name == "demo"
    assert not root.exists()


# --- new_asset -------------------------------------------------------------------------------


@pytest.mark.parametrize("asset_type", ASSET_TYPES)
def test_new_asset_renders_and_validates_cleanly(tmp_path: Path, asset_type: AssetType) -> None:
    root = _init(tmp_path)
    summary = api.new_asset(root, asset_type, f"a_{asset_type}")
    assert summary.asset_type == asset_type
    assert summary.frame_count > 0

    render = api.render_asset(root, f"a_{asset_type}")
    assert render.frames_written > 0
    assert not render.skipped

    report = api.validate_asset(root, f"a_{asset_type}")
    assert report.blocking is False
    assert report.error_count == 0


def test_new_asset_dry_run_writes_nothing(tmp_path: Path) -> None:
    root = _init(tmp_path)
    summary = api.new_asset(root, "character", "ghost", dry_run=True)
    assert summary.asset_id == "ghost"
    assert not (root / "assets" / "ghost").exists()
    assert "ghost" not in Project.load(root).discover_assets()


# --- list_assets / get_asset / inspect_asset -------------------------------------------------


def test_list_and_get_and_inspect_round_trip(tmp_path: Path) -> None:
    root = _init(tmp_path)
    api.new_asset(root, "character", "hero")
    api.new_asset(root, "prop", "beacon")

    summaries = api.list_assets(root)
    assert {s.asset_id for s in summaries} == {"hero", "beacon"}

    doc = api.get_asset(root, "hero")
    assert doc.asset.id == "hero"
    assert doc.asset.type == "character"

    inspection = api.inspect_asset(root, "hero")
    assert inspection.asset_id == "hero"
    assert inspection.regions.keys() == {"block"}
    assert inspection.animations.keys() == {"idle"}
    assert inspection.revision_count == 0
    assert inspection.head_revision is None


def test_get_asset_missing_id_lists_available(tmp_path: Path) -> None:
    root = _init(tmp_path)
    api.new_asset(root, "character", "hero")
    with pytest.raises(AssetNotFoundError) as exc_info:
        api.get_asset(root, "nope")
    assert "hero" in str(exc_info.value)


# --- render_asset ------------------------------------------------------------------------------


def test_render_asset_writes_expected_files_and_caches(tmp_path: Path) -> None:
    root = _init(tmp_path)
    api.new_asset(root, "character", "hero")

    first = api.render_asset(root, "hero")
    assert first.skipped is False
    assert first.frames_written == 2
    for rel in [*first.frame_paths, first.sheet_path, first.contact_sheet_path]:
        assert rel is not None
        assert (root / rel).is_file()

    second = api.render_asset(root, "hero")
    assert second.skipped is True
    assert second.frames_written == 0

    forced = api.render_asset(root, "hero", force=True)
    assert forced.skipped is False
    assert forced.frames_written == 2


def test_render_asset_dry_run_writes_nothing(tmp_path: Path) -> None:
    root = _init(tmp_path)
    api.new_asset(root, "character", "hero")
    result = api.render_asset(root, "hero", dry_run=True)
    assert result.dry_run is True
    assert result.skipped is False
    assert result.frames_written == 2
    assert not (root / "build" / "hero").exists()


def test_render_is_deterministic(tmp_path: Path) -> None:
    root = _init(tmp_path)
    api.new_asset(root, "character", "hero")
    first = api.render_asset(root, "hero")
    assert first.sheet_path is not None
    sheet_bytes_1 = (root / first.sheet_path).read_bytes()
    frame_bytes_1 = (root / first.frame_paths[0]).read_bytes()

    api.render_asset(root, "hero", force=True)
    second = api.render_asset(root, "hero", force=True)
    assert second.sheet_path is not None
    sheet_bytes_2 = (root / second.sheet_path).read_bytes()
    frame_bytes_2 = (root / second.frame_paths[0]).read_bytes()

    assert sheet_bytes_1 == sheet_bytes_2
    assert frame_bytes_1 == frame_bytes_2


# --- generate_preview --------------------------------------------------------------------------


def test_generate_preview_writes_one_file_per_animation_direction(tmp_path: Path) -> None:
    root = _init(tmp_path)
    _make_two_direction_asset(root, "hero")
    result = api.generate_preview(root, "hero")
    assert set(result.preview_paths) == {"idle_south", "idle_north"}
    for rel in result.preview_paths.values():
        assert (root / rel).is_file()
    assert result.preview_paths["idle_south"].endswith("preview_idle_south.gif")
    assert result.preview_paths["idle_north"].endswith("preview_idle_north.gif")


def test_generate_preview_dry_run_writes_nothing(tmp_path: Path) -> None:
    root = _init(tmp_path)
    api.new_asset(root, "character", "hero")
    result = api.generate_preview(root, "hero", dry_run=True)
    assert result.dry_run is True
    for rel in result.preview_paths.values():
        assert not (root / rel).exists()


# --- extract_palette_from_png / render_view / render_annotated_contact ------------------------


def test_extract_palette_from_png_orders_by_count_then_hex(tmp_path: Path) -> None:
    root = _init(tmp_path)
    png_path = root / "source.png"
    img = Image.new("RGBA", (3, 1), (0, 0, 0, 0))
    img.putpixel((0, 0), (255, 0, 0, 255))
    img.putpixel((1, 0), (0, 255, 0, 255))
    img.putpixel((2, 0), (0, 255, 0, 255))  # green appears twice -> ranks first
    img.save(png_path)

    palette = api.extract_palette_from_png(root, "source.png")
    assert [c.hex for c in palette.colors] == ["#00ff00", "#ff0000"]
    assert [c.id for c in palette.colors] == ["c00", "c01"]


def test_extract_palette_from_png_hostile_path_raises(tmp_path: Path) -> None:
    root = _init(tmp_path)
    with pytest.raises(PathSecurityError):
        api.extract_palette_from_png(root, "../outside.png")


def test_render_view_writes_annotated_png_at_expected_size(tmp_path: Path) -> None:
    root = _init(tmp_path)
    api.new_asset(root, "character", "hero")

    result = api.render_view(root, "hero", animation="idle", direction="south", scale=8)
    assert result.asset_id == "hero"
    assert result.path == "build/hero/view_idle_south_0.png"
    out = root / result.path
    assert out.is_file()
    with Image.open(out) as img:
        assert img.size == (result.width, result.height) == (32 * 8, 32 * 8)


def test_render_view_unknown_frame_raises(tmp_path: Path) -> None:
    root = _init(tmp_path)
    api.new_asset(root, "character", "hero")
    with pytest.raises(ForgeError, match="idle"):
        api.render_view(root, "hero", animation="idle", direction="north")


def test_render_view_rejects_terrain_asset(tmp_path: Path) -> None:
    root = _init(tmp_path)
    api.new_asset(root, "terrain", "ground")
    with pytest.raises(ForgeError, match="terrain"):
        api.render_view(root, "ground", animation="idle", direction="south")


def test_render_annotated_contact_writes_png_matching_reported_size(tmp_path: Path) -> None:
    root = _init(tmp_path)
    api.new_asset(root, "character", "hero")

    result = api.render_annotated_contact(root, "hero", scale=4)
    assert result.path == "build/hero/hero_annotated.png"
    out = root / result.path
    assert out.is_file()
    with Image.open(out) as img:
        assert img.size == (result.width, result.height)
    assert result.width > 32 * 4  # bigger than the raw scaled sheet: label gutter + separators


# --- export_godot ------------------------------------------------------------------------------


def test_export_godot_writes_and_round_trips(tmp_path: Path) -> None:
    root = _init(tmp_path)
    api.new_asset(root, "character", "hero")
    api.render_asset(root, "hero")
    manifest = api.export_godot(root, "hero")

    forge_path = root / "build" / "godot" / "hero.forge.json"
    assert forge_path.is_file()
    reparsed = GodotManifest.model_validate_json(forge_path.read_text())
    assert reparsed == manifest


def test_export_godot_without_render_raises(tmp_path: Path) -> None:
    root = _init(tmp_path)
    api.new_asset(root, "character", "hero")
    with pytest.raises(ExportError, match="render_asset"):
        api.export_godot(root, "hero")


# --- apply_asset_operation / revisions --------------------------------------------------------


def test_apply_asset_operation_mutates_and_invalidates_build(tmp_path: Path) -> None:
    root = _init(tmp_path)
    api.new_asset(root, "character", "hero")
    rendered = api.render_asset(root, "hero")
    assert rendered.skipped is False

    op = OperationSpec(name="translate_region", params={"region": "block", "offset": [1, 0]})
    record = api.apply_asset_operation(root, "hero", op, timestamp="2026-01-01T00:00:00Z")
    assert record.asset_id == "hero"
    assert record.hash_before != record.hash_after
    assert record.affected_regions == ["block"]

    revisions = api.list_asset_revisions(root, "hero")
    assert [r.revision_id for r in revisions] == [record.revision_id]

    doc = api.get_asset(root, "hero")
    assert doc.regions["block"].shapes[0].at == (-3, -4)  # type: ignore[union-attr]

    # The build hash is content-derived, so the stale build/hero manifest no longer
    # matches the edited spec: the next render must not skip.
    next_render = api.render_asset(root, "hero")
    assert next_render.skipped is False


def test_apply_asset_operation_persists_validation_report(tmp_path: Path) -> None:
    root = _init(tmp_path)
    api.new_asset(root, "character", "hero")

    # Push the region entirely off-canvas so validation finds a real (blocking) problem.
    op = OperationSpec(
        name="translate_region", params={"region": "block", "offset": [-1000, -1000]}
    )
    record = api.apply_asset_operation(root, "hero", op, timestamp="2026-01-01T00:00:00Z")
    assert record.validation is not None
    assert record.validation.blocking is True
    assert any(f.rule_id == "PIX008" for f in record.validation.findings)

    persisted = api.list_asset_revisions(root, "hero")[0].validation
    assert persisted is not None
    assert persisted.blocking is True
    assert [f.rule_id for f in persisted.findings] == [
        f.rule_id for f in record.validation.findings
    ]


def test_apply_asset_operation_dry_run_leaves_spec_untouched(tmp_path: Path) -> None:
    root = _init(tmp_path)
    api.new_asset(root, "character", "hero")
    spec_path = root / "assets" / "hero" / "hero.yaml"
    before = spec_path.read_bytes()

    op = OperationSpec(name="translate_region", params={"region": "block", "offset": [1, 0]})
    record = api.apply_asset_operation(
        root, "hero", op, timestamp="2026-01-01T00:00:00Z", dry_run=True
    )
    assert record.hash_before != record.hash_after

    after = spec_path.read_bytes()
    assert before == after
    assert api.list_asset_revisions(root, "hero") == []


# --- update_asset_spec ------------------------------------------------------------------------


def test_update_asset_spec_replaces_document_and_records_revision(tmp_path: Path) -> None:
    root = _init(tmp_path)
    api.new_asset(root, "character", "hero")
    spec = api.get_asset(root, "hero").model_dump(mode="json")
    spec["directions"] = ["south", "north"]

    record = api.update_asset_spec(root, "hero", spec, timestamp="2026-01-01T00:00:00Z")
    assert record.operation.name == "replace_spec"
    assert record.hash_before != record.hash_after

    updated = api.get_asset(root, "hero")
    assert updated.directions == ["south", "north"]


def test_update_asset_spec_rejects_asset_id_change(tmp_path: Path) -> None:
    root = _init(tmp_path)
    api.new_asset(root, "character", "hero")
    spec = api.get_asset(root, "hero").model_dump(mode="json")
    spec["asset"]["id"] = "villain"
    with pytest.raises(ForgeError):
        api.update_asset_spec(root, "hero", spec, timestamp="2026-01-01T00:00:00Z")


def test_update_asset_spec_dry_run_leaves_spec_untouched(tmp_path: Path) -> None:
    root = _init(tmp_path)
    api.new_asset(root, "character", "hero")
    spec_path = root / "assets" / "hero" / "hero.yaml"
    before = spec_path.read_bytes()
    spec = api.get_asset(root, "hero").model_dump(mode="json")
    spec["directions"] = ["south", "north"]

    api.update_asset_spec(root, "hero", spec, timestamp="2026-01-01T00:00:00Z", dry_run=True)
    assert spec_path.read_bytes() == before


def test_compare_asset_revisions_after_two_operations(tmp_path: Path) -> None:
    root = _init(tmp_path)
    api.new_asset(root, "character", "hero")

    op_a = OperationSpec(name="translate_region", params={"region": "block", "offset": [1, 0]})
    rev_a = api.apply_asset_operation(root, "hero", op_a, timestamp="2026-01-01T00:00:00Z")
    op_b = OperationSpec(
        name="set_frame_duration", params={"animation": "idle", "duration_ms": 150}
    )
    rev_b = api.apply_asset_operation(root, "hero", op_b, timestamp="2026-01-01T00:00:01Z")

    diff = api.compare_asset_revisions(root, "hero", rev_a.revision_id, rev_b.revision_id)
    assert diff.revision_a == rev_a.revision_id
    assert diff.revision_b == rev_b.revision_id
    assert [op.name for op in diff.operations] == ["set_frame_duration"]
    assert diff.hash_b == rev_b.hash_after


# --- test_seams ----------------------------------------------------------------------------------


def test_seams_on_terrain_asset(tmp_path: Path) -> None:
    root = _init(tmp_path)
    api.new_asset(root, "terrain", "ground")
    report = api.test_seams(root, "ground")
    assert report.asset_id == "ground"
    assert report.worst_mismatch == 0  # blank tiles tile seamlessly against themselves
    assert report.seam_map_path is not None
    assert (root / report.seam_map_path).is_file()


# --- list_operations -------------------------------------------------------------------------


def test_list_operations_matches_revisions_registry(tmp_path: Path) -> None:
    ops = api.list_operations()
    names = {op.name for op in ops}
    assert "translate_region" in names
    assert "resize_region" in names
    assert all(op.description for op in ops)


# --- build_asset / build_all --------------------------------------------------------------------


def test_build_asset_writes_full_manifest_and_caches(tmp_path: Path) -> None:
    root = _init(tmp_path)
    api.new_asset(root, "character", "hero")
    manifest = api.build_asset(root, "hero")
    assert manifest.validation_summary.blocking is False
    assert "godot" in manifest.output_paths
    assert "sheet" in manifest.output_paths
    assert manifest.preview_paths

    manifest_path = root / "build" / "hero" / "manifest.json"
    assert manifest_path.is_file()

    cached = api.build_asset(root, "hero")
    assert cached == manifest

    forced = api.build_asset(root, "hero", force=True)
    assert forced.spec_hash == manifest.spec_hash


def test_build_all_reports_broken_asset(tmp_path: Path) -> None:
    root = _init(tmp_path)
    api.new_asset(root, "character", "hero")
    api.new_asset(root, "terrain", "ground")
    _make_broken_asset(root, "cursed")

    report = api.build_all(root)
    assert {m.asset_id for m in report.assets} == {"hero", "ground"}
    assert report.blocking is True
    assert report.failed == ["cursed"]


# --- style profile / references -----------------------------------------------------------------


def test_style_profile_get_and_set(tmp_path: Path) -> None:
    root = _init(tmp_path)
    profile = api.get_style_profile(root)
    assert profile.perspective == ""

    updated = api.set_style_profile(root, {"perspective": "top_down"})
    assert updated.perspective == "top_down"
    assert api.get_style_profile(root).perspective == "top_down"


def test_scaffold_project_references(tmp_path: Path) -> None:
    root = _init(tmp_path)
    dirs = api.scaffold_project_references(root)
    names = {p.name for p in dirs}
    assert names == {"approved", "inspiration", "palettes", "animation", "rejected"}
    for d in dirs:
        assert (d / "README.md").is_file()


# --- path safety: every asset_id-taking entry point ---------------------------------------------


def test_hostile_asset_id_raises_path_security_error(tmp_path: Path) -> None:
    root = _init(tmp_path)
    api.new_asset(root, "character", "hero")
    hostile = "../evil"
    op = OperationSpec(name="translate_region", params={"region": "block", "offset": [1, 0]})

    calls = [
        lambda: api.get_asset(root, hostile),
        lambda: api.render_asset(root, hostile),
        lambda: api.validate_asset(root, hostile),
        lambda: api.generate_preview(root, hostile),
        lambda: api.export_godot(root, hostile),
        lambda: api.apply_asset_operation(root, hostile, op, timestamp="t"),
        lambda: api.compare_asset_revisions(root, hostile, "a", "b"),
        lambda: api.list_asset_revisions(root, hostile),
        lambda: api.inspect_asset(root, hostile),
        lambda: api.test_seams(root, hostile),
        lambda: api.build_asset(root, hostile),
        lambda: api.new_asset(root, "character", hostile),
    ]
    for call in calls:
        with pytest.raises(PathSecurityError):
            call()
