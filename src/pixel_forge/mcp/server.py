"""MCP server exposing `pixel_forge.api` as tools for AI agents.

Every tool below validates its input, calls exactly one `pixel_forge.api` function
(or, for `update_asset_spec`, a thin composition of `api.py` internals — see that
tool's docstring for why), and returns the called function's pydantic result model
unchanged. No rendering, validation, or revision logic lives in this module.

The project root is fixed once at process startup (`main()`, from a CLI arg or the
`PIXEL_FORGE_PROJECT` env var) and is never a per-tool parameter, so a calling agent
cannot point any tool outside the project the server was launched against. Every
asset id still flows through `api.py`'s own `validate_asset_id`/`safe_join` checks,
which reject path traversal (e.g. `"../evil"`) before touching the filesystem.

Uses the `mcp` package's `MCPServer` — the high-level ergonomic server class this
installed version (`mcp==2.0.0`) ships. There is no `mcp.server.fastmcp.FastMCP` in
this version; `MCPServer` is its direct successor and has the same
`@server.tool()`-decorator shape.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS
from pydantic import ValidationError

from pixel_forge import api
from pixel_forge.api import (
    AssetInspection,
    AssetSummary,
    BuildReport,
    OperationInfo,
    PreviewResult,
    RenderResult,
    SeamReport,
)
from pixel_forge.errors import ForgeError
from pixel_forge.rendering import render_asset_frames, render_terrain_tiles
from pixel_forge.revisions import record_revision
from pixel_forge.schemas import (
    AssetDocUnion,
    AssetType,
    GodotManifest,
    JSONValue,
    OperationSpec,
    ProjectConfig,
    ProvenanceEntry,
    RevisionDiff,
    RevisionRecord,
    StyleProfile,
    TerrainAsset,
    ValidationReport,
    parse_asset_doc,
)

# --- project root, fixed at startup, never a tool parameter --------------------------------

_project_root: Path | None = None


def set_project_root(root: Path) -> None:
    """Fix the project root every tool call operates within. Call once at startup."""
    global _project_root
    _project_root = Path(root).resolve()


def _root() -> Path:
    if _project_root is None:
        raise RuntimeError(
            "pixel-forge MCP server: no project root configured; call set_project_root() "
            "or start the server via main()"
        )
    return _project_root


# --- error translation: ForgeError -> a structured MCP error, never a bare traceback --------


def _guard[T](fn: Callable[[], T]) -> T:
    """Run `fn`, translating any `ForgeError` into a structured `MCPError`.

    `ForgeError` messages already name the problem and the fix (e.g. `AssetNotFoundError`
    lists known asset ids, `ExportError` names the missing render step), so the MCPError
    message is passed through verbatim. Any other exception is left to the SDK's own
    `Tool.run`, which wraps it as a non-protocol tool-execution error without leaking a
    traceback.
    """
    try:
        return fn()
    except ForgeError as exc:
        raise MCPError(code=INVALID_PARAMS, message=str(exc)) from exc


# --- server instance -------------------------------------------------------------------------

mcp_server: MCPServer = MCPServer(name="pixel-forge", version="0.1.0")


# --- project lifecycle -----------------------------------------------------------------------


@mcp_server.tool()
def initialize_asset_project(name: str, dry_run: bool = False) -> ProjectConfig:
    """Create (or verify) the pixel-forge project at the server's fixed root: writes
    `pixel-forge.yaml` and the `assets/`, `build/`, `references/` directories.

    Idempotent: calling it again with the same `name` on an already-initialized
    project returns the existing config unchanged; a different `name` on an existing
    project is an error. Call this once before any other tool if the project root
    does not yet contain a `pixel-forge.yaml`.
    """
    return _guard(lambda: api.init_project(_root(), name, dry_run=dry_run))


@mcp_server.tool()
def list_assets() -> list[AssetSummary]:
    """List every asset defined in the project: id, type, spec path, animations,
    directions, frame count, and spec hash. Use this to discover which asset ids
    exist before calling any asset_id-scoped tool.
    """
    return _guard(lambda: api.list_assets(_root()))


@mcp_server.tool()
def get_asset(asset_id: str) -> AssetDocUnion:
    """Load and return the full parsed spec document for one asset: regions,
    anchors, animations, palette, export/validation options, and (for terrain)
    tiles. Use this to inspect an asset's current definition before editing it with
    `apply_asset_operation` or `update_asset_spec`.
    """
    return _guard(lambda: api.get_asset(_root(), asset_id))


@mcp_server.tool()
def create_asset(asset_type: AssetType, asset_id: str, dry_run: bool = False) -> AssetSummary:
    """Create a new asset from a minimal starter template for the given type
    (character, enemy, prop, or terrain) and save its spec under `assets/<asset_id>/`.

    Fails if an asset with this id already exists. The template is deliberately
    minimal (a single region/animation for sprites, two blank tiles for terrain) and
    is guaranteed to render and validate with zero blocking findings; build it out
    afterwards with `apply_asset_operation` or `update_asset_spec`.
    """
    return _guard(lambda: api.new_asset(_root(), asset_type, asset_id, dry_run=dry_run))


def _update_asset_spec(
    root: Path, asset_id: str, spec: dict[str, JSONValue], *, timestamp: str
) -> RevisionRecord:
    # ponytail: api.py has no public "replace the whole spec document and record it
    # as a revision" function — apply_asset_operation only runs the named operations
    # in revisions/operations.py's registry, which "replace_spec" is deliberately not
    # part of. This composes api.py's own private load/validate helpers plus
    # revisions.record_revision the same way api.apply_asset_operation does
    # internally. If the CLI ever needs the same whole-document-replace behaviour,
    # promote this to a real `api.update_asset_spec` function instead of duplicating it.
    project = api._project(root)
    doc_before = api._load_doc(project, asset_id)
    try:
        doc_after = parse_asset_doc(dict(spec))
    except ForgeError:
        raise
    except ValidationError as exc:
        raise ForgeError(
            f"asset {asset_id!r}: replacement spec failed schema validation: {exc}"
        ) from exc
    if doc_after.asset.id != asset_id:
        raise ForgeError(
            f"update_asset_spec cannot change asset.id from {asset_id!r} to "
            f"{doc_after.asset.id!r}; create a new asset instead"
        )
    if isinstance(doc_after, TerrainAsset):
        report = api._validation_report(doc_after, {}, render_terrain_tiles(doc_after))
    else:
        report = api._validation_report(doc_after, render_asset_frames(doc_after), {})
    op = OperationSpec(name="replace_spec", params={"spec": doc_after.model_dump(mode="json")})
    inverse = OperationSpec(
        name="replace_spec", params={"spec": doc_before.model_dump(mode="json")}
    )
    record = record_revision(
        project.paths,
        asset_id,
        operation=op,
        inverse=inverse,
        doc_before=doc_before,
        doc_after=doc_after,
        timestamp=timestamp,
        validation=report,
    )
    project.save_asset(doc_after)
    return record


@mcp_server.tool()
def update_asset_spec(asset_id: str, spec: dict[str, JSONValue], timestamp: str) -> RevisionRecord:
    """Replace an asset's entire spec document in one shot and record the change as
    an auditable revision (operation name "replace_spec"), the same way every other
    edit goes through the revision log.

    Use this for structural edits that don't fit the operation DSL (adding a region,
    changing directions, editing palette colors); use `apply_asset_operation` for the
    smaller, invertible edits it already knows how to do. `spec` is the full document
    exactly as it appears in the asset's YAML file (no "kind" field — that is derived
    from `asset.type`). Rejects a spec whose `asset.id` does not match `asset_id`
    (create a new asset for that instead) and rejects a spec that fails schema
    validation.

    Example:
        current = get_asset(asset_id="hero")
        spec = current.model_dump(mode="json")
        spec["directions"].append("north")
        update_asset_spec(asset_id="hero", spec=spec, timestamp="2026-08-05T12:00:00Z")
    """
    return _guard(lambda: _update_asset_spec(_root(), asset_id, spec, timestamp=timestamp))


# --- rendering ---------------------------------------------------------------------------------


@mcp_server.tool()
def render_asset(asset_id: str, force: bool = False, dry_run: bool = False) -> RenderResult:
    """Render an asset's spec into frame PNGs, a sprite sheet, and a contact sheet
    under `build/<asset_id>/`.

    Idempotent: a second call against an unchanged spec with `force=False` is a
    no-op (`result.skipped` is `True`), because rendering is cached against the
    spec's content hash. Pass `force=True` to re-render anyway. Call this before
    `export_asset_to_godot`, which requires a texture already on disk.
    """
    return _guard(lambda: api.render_asset(_root(), asset_id, force=force, dry_run=dry_run))


@mcp_server.tool()
def validate_asset(asset_id: str) -> ValidationReport:
    """Run every deterministic and heuristic validation rule against an asset's
    current spec (re-rendering frames/tiles in memory; does not require a prior
    `render_asset` call and writes nothing to disk).

    Returns every finding; `report.blocking` is `True` if any finding has severity
    "error". Call this after any edit to confirm it didn't break pixel, animation, or
    tileset integrity.
    """
    return _guard(lambda: api.validate_asset(_root(), asset_id))


@mcp_server.tool()
def generate_preview(
    asset_id: str,
    fmt: Literal["gif", "webp"] | None = None,
    scale: int = 1,
    dry_run: bool = False,
) -> PreviewResult:
    """Render one animated preview (GIF or WebP) per (animation, direction) pair for
    a character, enemy, or prop asset, under `build/<asset_id>/`. Not valid for
    terrain assets — raises for those.

    `fmt` defaults to the asset's own `export.preview_format`. Rendering is a pure
    function of the spec (two calls against an unchanged spec produce byte-identical
    files) but is not cached like `render_asset` — it always re-renders.
    """
    return _guard(
        lambda: api.generate_preview(_root(), asset_id, fmt=fmt, scale=scale, dry_run=dry_run)
    )


@mcp_server.tool()
def export_asset_to_godot(asset_id: str, dry_run: bool = False) -> GodotManifest:
    """Build the Godot 4 import manifest (`build/godot/<asset_id>.forge.json`)
    describing textures, sprite frames, pivots, tileset/terrain data, and import
    settings for one asset.

    Idempotent: re-running it against an unchanged spec overwrites the manifest with
    byte-identical content. Precondition: call `render_asset` (or
    `build_asset_family`) first — this raises if the asset's texture has not been
    rendered to disk yet.
    """
    return _guard(lambda: api.export_godot(_root(), asset_id, dry_run=dry_run))


@mcp_server.tool()
def build_asset_family(force: bool = False) -> BuildReport:
    """Render, preview, and export every asset in the project in one call (render +
    generate_preview + export_asset_to_godot per asset), skipping assets whose build
    is already up to date with their spec hash unless `force=True`.

    Idempotent: safe to call repeatedly. Returns a report listing every asset built,
    which ones failed or carry blocking validation errors, and the total finding
    count. Use this before a Godot import pass to make sure every asset's artifacts
    exist.
    """
    return _guard(lambda: api.build_all(_root(), force=force))


def _get_validation_report(root: Path, asset_id: str) -> ValidationReport:
    # ponytail: `build/<id>/manifest.json` (AssetManifest) only persists a
    # ValidationSummary (counts), never the findings list, so it cannot serve as "the
    # last persisted report". The only place a full ValidationReport is ever written
    # to disk is RevisionRecord.validation in the revision log. "Last persisted
    # report" is read here as the newest revision that carries one; if AssetManifest
    # grows a persisted full report, prefer that instead.
    for record in reversed(api.list_asset_revisions(root, asset_id)):
        if record.validation is not None:
            return record.validation
    return api.validate_asset(root, asset_id)


@mcp_server.tool()
def get_validation_report(asset_id: str) -> ValidationReport:
    """Return the most recently persisted validation report for an asset (from its
    revision history) if one exists, else run `validate_asset` fresh.

    Cheaper than `validate_asset` when nothing has changed since the last edit; call
    `validate_asset` directly for a guaranteed up-to-date check against the current
    spec on disk.
    """
    return _guard(lambda: _get_validation_report(_root(), asset_id))


# --- revisions -----------------------------------------------------------------------------


@mcp_server.tool()
def apply_asset_operation(
    asset_id: str, op: OperationSpec, timestamp: str, dry_run: bool = False
) -> RevisionRecord:
    """Apply one revision operation (resize_region, translate_region, recolor_region,
    set_frame_duration, add_frame, remove_frame, set_region_visibility) to an
    asset's spec and record the change as a new, invertible revision.

    Call `list_operations` first to see the available operation names and their
    params. Not idempotent: each call appends a new revision, even if two calls carry
    an identical operation. With `dry_run=True`, returns the record that *would* be
    written without touching the spec file or the revision log.

    Example:
        apply_asset_operation(
            asset_id="hero",
            op={"name": "translate_region", "params": {"region": "block", "offset": [1, 0]}},
            timestamp="2026-08-05T12:00:00Z",
        )
    """
    return _guard(
        lambda: api.apply_asset_operation(
            _root(), asset_id, op, timestamp=timestamp, dry_run=dry_run
        )
    )


@mcp_server.tool()
def compare_revisions(asset_id: str, revision_a: str, revision_b: str) -> RevisionDiff:
    """Diff two revisions of an asset: the operations applied between them (in
    order) and the union of regions/frames/directions they touched.

    Both ids must already exist in the asset's revision log (see `list_revisions`).
    Order-independent — pass the two ids in either order.
    """
    return _guard(lambda: api.compare_asset_revisions(_root(), asset_id, revision_a, revision_b))


@mcp_server.tool()
def list_revisions(asset_id: str) -> list[RevisionRecord]:
    """List every revision recorded for an asset, oldest first, each with its
    operation, inverse, before/after hashes, and (if computed at the time) a
    validation report.

    Use this to find revision ids for `compare_revisions` or to audit an asset's
    edit history.
    """
    return _guard(lambda: api.list_asset_revisions(_root(), asset_id))


@mcp_server.tool()
def list_operations() -> list[OperationInfo]:
    """List every revision operation `apply_asset_operation` understands: its name,
    a description, and its named params.

    Call this before constructing an `OperationSpec` so the params line up with what
    the operation actually reads. Does not depend on the project root or any asset.
    """
    return _guard(api.list_operations)


# --- inspection / seams --------------------------------------------------------------------------


@mcp_server.tool()
def inspect_asset(asset_id: str) -> AssetInspection:
    """Return a structured overview of an asset: animations (frame counts,
    durations, events), regions (anchor/layer/shape count/protected), anchors,
    palette size, revision count and head revision id, and any output paths recorded
    by the last build.

    Cheaper than `get_asset` when an overview is enough — it does not return the
    full shape/region geometry.
    """
    return _guard(lambda: api.inspect_asset(_root(), asset_id))


@mcp_server.tool()
def test_seams(asset_id: str) -> SeamReport:
    """Render every tile of a terrain asset and check whether its edges tile
    seamlessly against themselves (adjacent-tile edge pixel comparison), writing a
    seam-map PNG under `build/<asset_id>/`.

    Only valid for terrain assets — raises for character/enemy/prop. `worst_mismatch`
    is the largest mismatched-pixel-run across every checked edge; 0 means every tile
    tiles cleanly against itself.
    """
    return _guard(lambda: api.test_seams(_root(), asset_id))


# --- references / style profile ------------------------------------------------------------------


@mcp_server.tool()
def get_style_profile() -> StyleProfile:
    """Return the project's style profile: perspective, palette tendencies, outline
    style, light direction, and similar parameters, plus reference provenance.

    Creates an empty profile on first call if none exists yet. Read this before
    generating or editing assets to keep new work consistent with the established
    style.
    """
    return _guard(lambda: api.get_style_profile(_root()))


@mcp_server.tool()
def update_style_profile(
    perspective: str | None = None,
    pixel_density: str | None = None,
    palette_tendencies: str | None = None,
    outline_style: str | None = None,
    light_direction: str | None = None,
    material_treatment: str | None = None,
    silhouette_complexity: str | None = None,
    texture_density: str | None = None,
    animation_timing: str | None = None,
    shape_language: str | None = None,
    environmental_hierarchy: str | None = None,
    provenance: list[ProvenanceEntry] | None = None,
) -> StyleProfile:
    """Shallow-merge the given fields into the project's style profile and append
    any new `provenance` entries (deduplicated by source_path+role), then save.

    Only the fields you pass are changed; omit a field (leave it `None`) to keep its
    current value. Call `scaffold_references` first if `references/` doesn't exist
    yet, and look at the image files under `references/{approved,inspiration,...}/`
    before calling this, so the observed style parameters are grounded in something.

    Example:
        update_style_profile(
            outline_style="1px dark outline, no anti-aliasing",
            light_direction="top-left",
            provenance=[{
                "source_path": "references/approved/hero_ref.png",
                "role": "approved",
                "notes": "outline treatment reference",
            }],
        )
    """
    changes: dict[str, str] = {
        key: value
        for key, value in {
            "perspective": perspective,
            "pixel_density": pixel_density,
            "palette_tendencies": palette_tendencies,
            "outline_style": outline_style,
            "light_direction": light_direction,
            "material_treatment": material_treatment,
            "silhouette_complexity": silhouette_complexity,
            "texture_density": texture_density,
            "animation_timing": animation_timing,
            "shape_language": shape_language,
            "environmental_hierarchy": environmental_hierarchy,
        }.items()
        if value is not None
    }
    return _guard(
        lambda: api.set_style_profile(_root(), changes, provenance=tuple(provenance or []))
    )


def _scaffold_reference_paths(root: Path) -> list[str]:
    dirs = api.scaffold_project_references(root)
    return sorted(d.relative_to(root).as_posix() for d in dirs)


@mcp_server.tool()
def scaffold_references() -> list[str]:
    """Ensure `references/{approved,inspiration,palettes,animation,rejected}` all
    exist (each with a README stating the no-tracing policy), and return their
    project-relative paths.

    Idempotent: never overwrites a file already there, in particular anything under
    `references/approved/`. Call this once early in a project so there is somewhere
    to drop reference art before calling `update_style_profile`.
    """
    return _guard(lambda: _scaffold_reference_paths(_root()))


# --- entry point -------------------------------------------------------------------------------


def main() -> None:
    """Entry point for `python -m pixel_forge.mcp.server`.

    Reads the project root from `argv[1]`, falling back to the `PIXEL_FORGE_PROJECT`
    env var, fixes it as this process's project root, and serves every registered
    tool over stdio until the client disconnects.
    """
    root_arg = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("PIXEL_FORGE_PROJECT")
    if not root_arg:
        print(
            "usage: python -m pixel_forge.mcp.server <project_root> (or set PIXEL_FORGE_PROJECT)",
            file=sys.stderr,
        )
        raise SystemExit(2)
    set_project_root(Path(root_arg))
    mcp_server.run()


if __name__ == "__main__":
    main()
