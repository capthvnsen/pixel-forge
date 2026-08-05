"""Integration tests for the MCP server's tool functions (`pixel_forge.mcp.server`).

Calls the registered tool functions directly — no subprocess, no stdio transport —
per Task 14's test instructions. `server.set_project_root(root)` stands in for what
`main()` does at process startup.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS

from pixel_forge import api
from pixel_forge.mcp import server
from pixel_forge.schemas import (
    AssetType,
    GodotManifest,
    OperationSpec,
    ProjectConfig,
    ProvenanceEntry,
    RevisionRecord,
    StyleProfile,
    ValidationReport,
)

ASSET_TYPES: tuple[AssetType, ...] = ("character", "enemy", "prop", "terrain")

EXPECTED_TOOL_NAMES = {
    "initialize_asset_project",
    "list_assets",
    "get_asset",
    "create_asset",
    "update_asset_spec",
    "apply_asset_operation",
    "pin_asset_source",
    "render_asset",
    "validate_asset",
    "generate_preview",
    "export_asset_to_godot",
    "build_asset_family",
    "get_validation_report",
    "compare_revisions",
    "list_revisions",
    "list_operations",
    "inspect_asset",
    "test_seams",
    "get_style_profile",
    "update_style_profile",
    "scaffold_references",
}


def _init(tmp_path: Path, name: str = "demo") -> Path:
    root = tmp_path / name
    api.init_project(root, name)
    server.set_project_root(root)
    return root


# --- registration / contract -----------------------------------------------------------------


def test_registered_tool_set_matches_expected_exactly() -> None:
    names = {t.name for t in server.mcp_server._tool_manager.list_tools()}
    assert names == EXPECTED_TOOL_NAMES


def test_every_tool_has_a_non_empty_schema_and_a_real_docstring() -> None:
    tools = server.mcp_server._tool_manager.list_tools()
    assert len(tools) == len(EXPECTED_TOOL_NAMES)
    for tool in tools:
        assert isinstance(tool.parameters, dict)
        assert tool.parameters, f"{tool.name} has no input schema"
        assert tool.description, f"{tool.name} has no docstring"
        assert len(tool.description) > 20, f"{tool.name} docstring is too thin to guide an agent"


# --- project lifecycle -----------------------------------------------------------------------


def test_initialize_asset_project_returns_config(tmp_path: Path) -> None:
    root = tmp_path / "demo"
    server.set_project_root(root)
    config = server.initialize_asset_project(name="demo")
    assert isinstance(config, ProjectConfig)
    assert config.name == "demo"
    assert (root / "pixel-forge.yaml").is_file()


def test_create_asset_for_all_four_types_then_render_and_validate_cleanly(tmp_path: Path) -> None:
    _init(tmp_path)
    for asset_type in ASSET_TYPES:
        asset_id = f"a_{asset_type}"
        summary = server.create_asset(asset_type=asset_type, asset_id=asset_id)
        assert summary.asset_type == asset_type
        assert summary.frame_count > 0

        render = server.render_asset(asset_id=asset_id)
        assert render.frames_written > 0
        assert render.skipped is False

        report = server.validate_asset(asset_id=asset_id)
        assert isinstance(report, ValidationReport)
        assert report.blocking is False
        assert report.error_count == 0


def test_list_assets_get_asset_and_inspect_asset_round_trip(tmp_path: Path) -> None:
    _init(tmp_path)
    server.create_asset(asset_type="character", asset_id="hero")

    summaries = server.list_assets()
    assert {s.asset_id for s in summaries} == {"hero"}

    doc = server.get_asset(asset_id="hero")
    assert doc.asset.id == "hero"
    assert doc.asset.type == "character"

    inspection = server.inspect_asset(asset_id="hero")
    assert inspection.asset_id == "hero"
    assert inspection.revision_count == 0
    assert inspection.head_revision is None


def test_render_asset_is_idempotent(tmp_path: Path) -> None:
    _init(tmp_path)
    server.create_asset(asset_type="character", asset_id="hero")

    first = server.render_asset(asset_id="hero")
    assert first.skipped is False
    second = server.render_asset(asset_id="hero")
    assert second.skipped is True
    forced = server.render_asset(asset_id="hero", force=True)
    assert forced.skipped is False


def test_generate_preview_writes_files(tmp_path: Path) -> None:
    root = _init(tmp_path)
    server.create_asset(asset_type="character", asset_id="hero")

    result = server.generate_preview(asset_id="hero")
    assert result.preview_paths
    for rel in result.preview_paths.values():
        assert (root / rel).is_file()


def test_export_asset_to_godot_after_render_parses_as_godot_manifest(tmp_path: Path) -> None:
    root = _init(tmp_path)
    server.create_asset(asset_type="character", asset_id="hero")
    server.render_asset(asset_id="hero")

    manifest = server.export_asset_to_godot(asset_id="hero")
    assert isinstance(manifest, GodotManifest)

    forge_path = root / "build" / "godot" / "hero.forge.json"
    assert forge_path.is_file()
    reparsed = GodotManifest.model_validate_json(forge_path.read_text())
    assert reparsed == manifest

    # Idempotent: re-running against an unchanged spec produces the same manifest.
    again = server.export_asset_to_godot(asset_id="hero")
    assert again == manifest


def test_export_asset_to_godot_without_render_raises_structured_error(tmp_path: Path) -> None:
    _init(tmp_path)
    server.create_asset(asset_type="character", asset_id="hero")

    with pytest.raises(MCPError) as exc_info:
        server.export_asset_to_godot(asset_id="hero")
    assert "render_asset" in exc_info.value.message


def test_build_asset_family_builds_every_asset_and_is_idempotent(tmp_path: Path) -> None:
    _init(tmp_path)
    server.create_asset(asset_type="character", asset_id="hero")
    server.create_asset(asset_type="terrain", asset_id="ground")

    report = server.build_asset_family()
    assert {m.asset_id for m in report.assets} == {"hero", "ground"}
    assert report.blocking is False

    cached = server.build_asset_family()
    assert cached.assets == report.assets


# --- revisions -----------------------------------------------------------------------------


def test_apply_asset_operation_mutates_spec_and_returns_revision_record(tmp_path: Path) -> None:
    _init(tmp_path)
    server.create_asset(asset_type="character", asset_id="hero")

    op = OperationSpec(name="translate_region", params={"region": "block", "offset": [1, 0]})
    record = server.apply_asset_operation(asset_id="hero", op=op, timestamp="2026-08-05T12:00:00Z")
    assert isinstance(record, RevisionRecord)
    assert record.asset_id == "hero"
    assert record.hash_before != record.hash_after

    doc = server.get_asset(asset_id="hero")
    assert doc.regions["block"].shapes[0].at == (-3, -4)  # type: ignore[union-attr]

    revisions = server.list_revisions(asset_id="hero")
    assert [r.revision_id for r in revisions] == [record.revision_id]


def test_compare_revisions_between_two_operations(tmp_path: Path) -> None:
    _init(tmp_path)
    server.create_asset(asset_type="character", asset_id="hero")

    op_a = OperationSpec(name="translate_region", params={"region": "block", "offset": [1, 0]})
    rev_a = server.apply_asset_operation(asset_id="hero", op=op_a, timestamp="2026-08-05T12:00:00Z")
    op_b = OperationSpec(
        name="set_frame_duration", params={"animation": "idle", "duration_ms": 150}
    )
    rev_b = server.apply_asset_operation(asset_id="hero", op=op_b, timestamp="2026-08-05T12:00:01Z")

    diff = server.compare_revisions(
        asset_id="hero", revision_a=rev_a.revision_id, revision_b=rev_b.revision_id
    )
    assert diff.revision_a == rev_a.revision_id
    assert diff.revision_b == rev_b.revision_id
    assert [op.name for op in diff.operations] == ["set_frame_duration"]


def test_list_operations_lists_known_operations() -> None:
    ops = server.list_operations()
    names = {op.name for op in ops}
    assert "translate_region" in names
    assert "resize_region" in names
    assert all(op.description for op in ops)


def test_get_validation_report_falls_back_to_fresh_when_no_revisions(tmp_path: Path) -> None:
    _init(tmp_path)
    server.create_asset(asset_type="character", asset_id="hero")

    fresh = server.validate_asset(asset_id="hero")
    reported = server.get_validation_report(asset_id="hero")
    assert reported.blocking == fresh.blocking
    assert reported.findings == fresh.findings


def test_get_validation_report_returns_persisted_report_from_revision_history(
    tmp_path: Path,
) -> None:
    _init(tmp_path)
    server.create_asset(asset_type="character", asset_id="hero")

    # Push the region off-canvas so the recorded revision carries a real blocking finding.
    op = OperationSpec(
        name="translate_region", params={"region": "block", "offset": [-1000, -1000]}
    )
    record = server.apply_asset_operation(asset_id="hero", op=op, timestamp="2026-08-05T12:00:00Z")
    assert record.validation is not None
    assert record.validation.blocking is True

    reported = server.get_validation_report(asset_id="hero")
    assert reported.blocking is True
    assert [f.rule_id for f in reported.findings] == [f.rule_id for f in record.validation.findings]


# --- update_asset_spec -----------------------------------------------------------------------


def test_update_asset_spec_replaces_document_and_records_revision(tmp_path: Path) -> None:
    _init(tmp_path)
    server.create_asset(asset_type="character", asset_id="hero")

    spec = server.get_asset(asset_id="hero").model_dump(mode="json")
    spec["directions"] = ["south", "north"]

    record = server.update_asset_spec(asset_id="hero", spec=spec, timestamp="2026-08-05T12:00:00Z")
    assert isinstance(record, RevisionRecord)
    assert record.operation.name == "replace_spec"
    assert record.asset_id == "hero"

    updated = server.get_asset(asset_id="hero")
    assert updated.directions == ["south", "north"]  # type: ignore[union-attr]

    revisions = server.list_revisions(asset_id="hero")
    assert [r.revision_id for r in revisions] == [record.revision_id]


def test_update_asset_spec_rejects_asset_id_change(tmp_path: Path) -> None:
    _init(tmp_path)
    server.create_asset(asset_type="character", asset_id="hero")

    spec = server.get_asset(asset_id="hero").model_dump(mode="json")
    spec["asset"]["id"] = "someone_else"

    with pytest.raises(MCPError) as exc_info:
        server.update_asset_spec(asset_id="hero", spec=spec, timestamp="2026-08-05T12:00:00Z")
    assert "someone_else" in exc_info.value.message


def test_update_asset_spec_rejects_schema_invalid_document(tmp_path: Path) -> None:
    _init(tmp_path)
    server.create_asset(asset_type="character", asset_id="hero")

    spec = server.get_asset(asset_id="hero").model_dump(mode="json")
    del spec["palette"]  # required field missing -> fails schema validation

    with pytest.raises(MCPError):
        server.update_asset_spec(asset_id="hero", spec=spec, timestamp="2026-08-05T12:00:00Z")


# --- seams / references / style profile --------------------------------------------------------


def test_seams_on_terrain_asset(tmp_path: Path) -> None:
    _init(tmp_path)
    server.create_asset(asset_type="terrain", asset_id="ground")

    report = server.test_seams(asset_id="ground")
    assert report.asset_id == "ground"
    assert report.worst_mismatch == 0
    assert report.seam_map_path is not None


def test_get_and_update_style_profile(tmp_path: Path) -> None:
    _init(tmp_path)
    profile = server.get_style_profile()
    assert isinstance(profile, StyleProfile)
    assert profile.perspective == ""

    updated = server.update_style_profile(
        outline_style="1px dark outline",
        light_direction="top-left",
        provenance=[ProvenanceEntry(source_path="references/approved/x.png", role="approved")],
    )
    assert updated.outline_style == "1px dark outline"
    assert updated.light_direction == "top-left"
    assert len(updated.provenance) == 1
    assert server.get_style_profile().outline_style == "1px dark outline"


def test_scaffold_references_creates_expected_dirs(tmp_path: Path) -> None:
    root = _init(tmp_path)
    paths = server.scaffold_references()
    assert set(paths) == {
        "references/approved",
        "references/inspiration",
        "references/palettes",
        "references/animation",
        "references/rejected",
    }
    for rel in paths:
        assert (root / rel / "README.md").is_file()


# --- error handling ----------------------------------------------------------------------------


def test_missing_asset_id_returns_structured_error_not_a_raw_exception(tmp_path: Path) -> None:
    _init(tmp_path)
    server.create_asset(asset_type="character", asset_id="hero")

    with pytest.raises(MCPError) as exc_info:
        server.get_asset(asset_id="does_not_exist")
    assert exc_info.value.code == INVALID_PARAMS
    assert "hero" in exc_info.value.message  # names the fix: which ids do exist


def test_hostile_asset_id_rejected_by_every_tool_that_takes_one(tmp_path: Path) -> None:
    _init(tmp_path)
    server.create_asset(asset_type="character", asset_id="hero")
    hostile = "../evil"
    op = OperationSpec(name="translate_region", params={"region": "block", "offset": [1, 0]})
    spec = server.get_asset(asset_id="hero").model_dump(mode="json")

    calls: list[Callable[[], object]] = [
        lambda: server.get_asset(asset_id=hostile),
        lambda: server.create_asset(asset_type="character", asset_id=hostile),
        lambda: server.update_asset_spec(asset_id=hostile, spec=spec, timestamp="t"),
        lambda: server.apply_asset_operation(asset_id=hostile, op=op, timestamp="t"),
        lambda: server.render_asset(asset_id=hostile),
        lambda: server.validate_asset(asset_id=hostile),
        lambda: server.generate_preview(asset_id=hostile),
        lambda: server.export_asset_to_godot(asset_id=hostile),
        lambda: server.get_validation_report(asset_id=hostile),
        lambda: server.compare_revisions(asset_id=hostile, revision_a="a", revision_b="b"),
        lambda: server.list_revisions(asset_id=hostile),
        lambda: server.inspect_asset(asset_id=hostile),
        lambda: server.test_seams(asset_id=hostile),
    ]
    for call in calls:
        with pytest.raises(MCPError):
            call()
