"""Command implementations: thin over `pixel_forge.api`.

Every function here parses its arguments, calls exactly one `api` function, and
renders the returned pydantic model. Domain logic (rendering, validation,
revision semantics, ...) lives in `api.py` and the packages it wires together —
nothing here duplicates it. Functions are registered onto the Typer app(s) in
`main.py`, not decorated here, so this module stays a plain function library.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

import typer
from pydantic import BaseModel, ValidationError

from pixel_forge import api
from pixel_forge.domain import load_yaml
from pixel_forge.errors import ForgeError
from pixel_forge.schemas import (
    AssetManifest,
    AssetType,
    GodotManifest,
    OperationSpec,
    ProvenanceEntry,
    RevisionDiff,
    RevisionRecord,
    StyleProfile,
    ValidationReport,
    export_json_schemas,
)

_ASSET_ID_HELP = (
    "Asset id. Accepts either the bare id ('engineer') or the spec-style path "
    "('assets/engineer') — both resolve to the same asset."
)
_ROOT_HELP = "Project root directory."


@dataclass(frozen=True)
class State:
    """Global output flags, stashed on `ctx.obj` by the app callback in `main.py`."""

    json: bool = False
    quiet: bool = False


def _state(ctx: typer.Context) -> State:
    return cast(State, ctx.obj)


def _emit[M: BaseModel](state: State, model: M, render_text: Callable[[M], str]) -> None:
    if state.json:
        typer.echo(model.model_dump_json(indent=2))
        return
    if state.quiet:
        return
    typer.echo(render_text(model))


def _emit_list[M: BaseModel](
    state: State, models: Sequence[M], render_text: Callable[[Sequence[M]], str]
) -> None:
    if state.json:
        typer.echo(json.dumps([m.model_dump(mode="json") for m in models], indent=2))
        return
    if state.quiet:
        return
    typer.echo(render_text(models))


def _emit_paths(state: State, paths: Sequence[Path]) -> None:
    rendered = [str(p) for p in paths]
    if state.json:
        typer.echo(json.dumps(rendered, indent=2))
        return
    if state.quiet:
        return
    for p in rendered:
        typer.echo(p)


def guarded[F: Callable[..., None]](func: F) -> F:
    """Translate `ForgeError` (and any other unexpected exception) into a clean exit.

    `typer.Exit`/`typer.BadParameter` raised deliberately by a command (usage
    errors, blocking-validation exits) pass straight through untouched — only
    genuine failures get converted here, always to exit 3, always without a
    traceback.
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> None:
        try:
            func(*args, **kwargs)
        except (typer.Exit, typer.BadParameter):
            raise
        except ForgeError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=3) from None
        except Exception as exc:
            typer.echo(f"internal error: {exc}", err=True)
            raise typer.Exit(code=3) from None

    return cast(F, wrapper)


def _normalize_asset_id(raw: str) -> str:
    """Accept both bare ids ('engineer') and spec-style paths ('assets/engineer/')."""
    value = raw.strip().rstrip("/")
    if value.startswith("assets/"):
        value = value[len("assets/") :]
    return value


def _parse_param_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _parse_key_value_pairs(pairs: Sequence[str], *, option_name: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for pair in pairs:
        key, sep, raw_value = pair.partition("=")
        if not sep:
            raise typer.BadParameter(f"must be KEY=VALUE, got {pair!r}", param_hint=option_name)
        result[key] = _parse_param_value(raw_value)
    return result


def _parse_provenance(entries: Sequence[str]) -> list[ProvenanceEntry]:
    result: list[ProvenanceEntry] = []
    for entry in entries:
        parts = entry.split(":", 2)
        if len(parts) < 2:
            raise typer.BadParameter(
                f"must be PATH:ROLE[:NOTES], got {entry!r}", param_hint="--provenance"
            )
        source_path, role, *rest = parts
        notes = rest[0] if rest else ""
        try:
            result.append(
                ProvenanceEntry.model_validate(
                    {"source_path": source_path, "role": role, "notes": notes}
                )
            )
        except ValidationError as exc:
            raise typer.BadParameter(
                f"invalid entry {entry!r}: {exc}", param_hint="--provenance"
            ) from exc
    return result


def _default_timestamp() -> str:
    """Current UTC time in ISO-8601.

    This is the only place in the codebase allowed to read the clock: `api.py`
    must stay deterministic, so every timestamp it uses enters as an explicit
    argument, supplied here at the CLI boundary.
    """
    return datetime.now(UTC).isoformat()


# --- project lifecycle ---------------------------------------------------------------------


def init_project_cmd(
    ctx: typer.Context,
    path: Path,
    name: str | None = typer.Option(
        None, "--name", help="Project name; defaults to the directory name."
    ),
) -> None:
    """Initialise a new pixel-forge project at PATH."""
    state = _state(ctx)
    resolved_name = name or path.resolve().name
    config = api.init_project(path, resolved_name)
    _emit(state, config, lambda c: f"initialised project {c.name!r} at {path}")


def new_asset_cmd(
    ctx: typer.Context,
    asset_type: AssetType,
    asset_id: str = typer.Argument(..., help=_ASSET_ID_HELP),
    root: Path = typer.Option(Path("."), "--root", help=_ROOT_HELP),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Compute the result without writing anything."
    ),
) -> None:
    """Create a new asset from the built-in starter template for ASSET_TYPE."""
    state = _state(ctx)
    resolved_id = _normalize_asset_id(asset_id)
    summary = api.new_asset(root, asset_type, resolved_id, dry_run=dry_run)
    _emit(state, summary, _render_summary)


def list_assets_cmd(
    ctx: typer.Context,
    root: Path = typer.Option(Path("."), "--root", help=_ROOT_HELP),
) -> None:
    """List every asset in the project."""
    state = _state(ctx)
    summaries = api.list_assets(root)
    _emit_list(state, summaries, _render_asset_table)


def inspect_asset_cmd(
    ctx: typer.Context,
    asset_id: str = typer.Argument(..., help=_ASSET_ID_HELP),
    root: Path = typer.Option(Path("."), "--root", help=_ROOT_HELP),
) -> None:
    """Show detailed structural information about one asset."""
    state = _state(ctx)
    resolved_id = _normalize_asset_id(asset_id)
    inspection = api.inspect_asset(root, resolved_id)
    _emit(state, inspection, _render_inspection)


# --- render / validate / preview / export --------------------------------------------------


def validate_asset_cmd(
    ctx: typer.Context,
    asset_id: str = typer.Argument(..., help=_ASSET_ID_HELP),
    root: Path = typer.Option(Path("."), "--root", help=_ROOT_HELP),
) -> None:
    """Run every validation rule against an asset."""
    state = _state(ctx)
    resolved_id = _normalize_asset_id(asset_id)
    report = api.validate_asset(root, resolved_id)
    _emit(state, report, ValidationReport.to_text)
    if report.blocking:
        raise typer.Exit(code=1)


def render_asset_cmd(
    ctx: typer.Context,
    asset_id: str = typer.Argument(..., help=_ASSET_ID_HELP),
    root: Path = typer.Option(Path("."), "--root", help=_ROOT_HELP),
    force: bool = typer.Option(False, "--force", help="Re-render even if the spec is unchanged."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Compute the result without writing anything."
    ),
) -> None:
    """Render an asset's frames, sprite sheet, and contact sheet."""
    state = _state(ctx)
    resolved_id = _normalize_asset_id(asset_id)
    result = api.render_asset(root, resolved_id, force=force, dry_run=dry_run)
    _emit(state, result, _render_render_result)


def preview_cmd(
    ctx: typer.Context,
    asset_id: str = typer.Argument(..., help=_ASSET_ID_HELP),
    root: Path = typer.Option(Path("."), "--root", help=_ROOT_HELP),
    fmt: Literal["gif", "webp"] | None = typer.Option(
        None, "--format", help="Defaults to the asset's configured preview format."
    ),
    scale: int = typer.Option(1, "--scale", min=1, help="Integer upscale factor."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Compute the result without writing anything."
    ),
) -> None:
    """Generate an animated preview (GIF/WebP) per animation."""
    state = _state(ctx)
    resolved_id = _normalize_asset_id(asset_id)
    result = api.generate_preview(root, resolved_id, fmt=fmt, scale=scale, dry_run=dry_run)
    _emit(state, result, _render_preview_result)


def export_godot_cmd(
    ctx: typer.Context,
    asset_id: str = typer.Argument(..., help=_ASSET_ID_HELP),
    root: Path = typer.Option(Path("."), "--root", help=_ROOT_HELP),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Compute the manifest without writing it."
    ),
) -> None:
    """Export a Godot 4.x import manifest for an already-rendered asset."""
    state = _state(ctx)
    resolved_id = _normalize_asset_id(asset_id)
    manifest = api.export_godot(root, resolved_id, dry_run=dry_run)
    _emit(state, manifest, _render_godot_manifest)


# --- revisions -------------------------------------------------------------------------------


def revise_cmd(
    ctx: typer.Context,
    asset_id: str = typer.Argument(..., help=_ASSET_ID_HELP),
    operation: str = typer.Option(
        ..., "--operation", help="Operation name; see `pixel-forge operations`."
    ),
    param: list[str] = typer.Option(
        [], "--param", help="Repeatable KEY=VALUE operation parameter (value parsed as JSON)."
    ),
    protect: list[str] = typer.Option(
        [], "--protect", help="Repeatable anchor/region name that must not change."
    ),
    timestamp: str | None = typer.Option(
        None, "--timestamp", help="ISO-8601 UTC timestamp for the revision; defaults to now."
    ),
    root: Path = typer.Option(Path("."), "--root", help=_ROOT_HELP),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Compute the revision without writing it."
    ),
) -> None:
    """Apply a revision operation to an asset's spec."""
    state = _state(ctx)
    resolved_id = _normalize_asset_id(asset_id)
    known = {info.name for info in api.list_operations()}
    if operation not in known:
        raise typer.BadParameter(
            f"unknown operation {operation!r}; available operations: {sorted(known)}",
            param_hint="--operation",
        )
    params = _parse_key_value_pairs(param, option_name="--param")
    op = OperationSpec(name=operation, params=params, protect=list(protect))
    resolved_timestamp = timestamp if timestamp is not None else _default_timestamp()
    record = api.apply_asset_operation(
        root, resolved_id, op, timestamp=resolved_timestamp, dry_run=dry_run
    )
    _emit(state, record, _render_revision_record)


def update_spec_cmd(
    ctx: typer.Context,
    asset_id: str = typer.Argument(..., help=_ASSET_ID_HELP),
    file: Path = typer.Option(
        ..., "--file", help="Path to a YAML file holding the replacement spec document."
    ),
    timestamp: str | None = typer.Option(
        None, "--timestamp", help="ISO-8601 UTC timestamp for the revision; defaults to now."
    ),
    root: Path = typer.Option(Path("."), "--root", help=_ROOT_HELP),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Compute the revision without writing it."
    ),
) -> None:
    """Replace an asset's entire spec document from FILE and record it as a revision."""
    state = _state(ctx)
    resolved_id = _normalize_asset_id(asset_id)
    spec = load_yaml(file)
    resolved_timestamp = timestamp if timestamp is not None else _default_timestamp()
    record = api.update_asset_spec(
        root, resolved_id, spec, timestamp=resolved_timestamp, dry_run=dry_run
    )
    _emit(state, record, _render_revision_record)


def operations_cmd(ctx: typer.Context) -> None:
    """List every revision operation this toolkit knows how to apply."""
    state = _state(ctx)
    infos = api.list_operations()
    _emit_list(state, infos, _render_operations_table)


def revisions_cmd(
    ctx: typer.Context,
    asset_id: str = typer.Argument(..., help=_ASSET_ID_HELP),
    root: Path = typer.Option(Path("."), "--root", help=_ROOT_HELP),
) -> None:
    """List an asset's revision history."""
    state = _state(ctx)
    resolved_id = _normalize_asset_id(asset_id)
    records = api.list_asset_revisions(root, resolved_id)
    _emit_list(state, records, _render_revisions_table)


def diff_cmd(
    ctx: typer.Context,
    asset_id: str = typer.Argument(..., help=_ASSET_ID_HELP),
    rev_a: str = typer.Argument(...),
    rev_b: str = typer.Argument(...),
    root: Path = typer.Option(Path("."), "--root", help=_ROOT_HELP),
) -> None:
    """Show the operations and affected targets between two revisions."""
    state = _state(ctx)
    resolved_id = _normalize_asset_id(asset_id)
    diff = api.compare_asset_revisions(root, resolved_id, rev_a, rev_b)
    _emit(state, diff, _render_diff)


# --- terrain / build ---------------------------------------------------------------------------


def test_seams_cmd(
    ctx: typer.Context,
    asset_id: str = typer.Argument(..., help=_ASSET_ID_HELP),
    root: Path = typer.Option(Path("."), "--root", help=_ROOT_HELP),
) -> None:
    """Check tile-edge seams for a terrain asset."""
    state = _state(ctx)
    resolved_id = _normalize_asset_id(asset_id)
    report = api.test_seams(root, resolved_id)
    _emit(state, report, _render_seam_report)


def build_cmd(
    ctx: typer.Context,
    asset_id: str = typer.Argument(..., help=_ASSET_ID_HELP),
    root: Path = typer.Option(Path("."), "--root", help=_ROOT_HELP),
    force: bool = typer.Option(False, "--force", help="Rebuild even if the spec is unchanged."),
) -> None:
    """Render, preview, and export one asset in a single step."""
    state = _state(ctx)
    resolved_id = _normalize_asset_id(asset_id)
    manifest = api.build_asset(root, resolved_id, force=force)
    _emit(state, manifest, _render_manifest)
    if manifest.validation_summary.blocking:
        raise typer.Exit(code=1)


def build_all_cmd(
    ctx: typer.Context,
    root: Path = typer.Option(Path("."), "--root", help=_ROOT_HELP),
    force: bool = typer.Option(False, "--force", help="Rebuild even if specs are unchanged."),
) -> None:
    """Build every asset in the project."""
    state = _state(ctx)
    report = api.build_all(root, force=force)
    _emit(state, report, _render_build_report)
    if report.blocking:
        raise typer.Exit(code=1)


# --- references / style / schemas ---------------------------------------------------------------


def references_init_cmd(
    ctx: typer.Context,
    root: Path = typer.Option(Path("."), "--root", help=_ROOT_HELP),
) -> None:
    """Scaffold the reference-art library directories."""
    state = _state(ctx)
    paths = api.scaffold_project_references(root)
    root_resolved = root.resolve()
    rel_paths = [p.relative_to(root_resolved) for p in paths]
    _emit_paths(state, rel_paths)


def style_show_cmd(
    ctx: typer.Context,
    root: Path = typer.Option(Path("."), "--root", help=_ROOT_HELP),
) -> None:
    """Show the project's style profile."""
    state = _state(ctx)
    profile = api.get_style_profile(root)
    _emit(state, profile, _render_style_profile)


def style_set_cmd(
    ctx: typer.Context,
    field: list[str] = typer.Option(
        [], "--field", help="Repeatable KEY=VALUE style field to set (value parsed as JSON)."
    ),
    provenance: list[str] = typer.Option(
        [], "--provenance", help="Repeatable PATH:ROLE[:NOTES] provenance entry."
    ),
    root: Path = typer.Option(Path("."), "--root", help=_ROOT_HELP),
) -> None:
    """Update fields on the project's style profile."""
    state = _state(ctx)
    changes = _parse_key_value_pairs(field, option_name="--field")
    entries = _parse_provenance(provenance)
    profile = api.set_style_profile(root, changes, provenance=entries)
    _emit(state, profile, _render_style_profile)


def schemas_export_cmd(
    ctx: typer.Context,
    out_dir: Path,
) -> None:
    """Write JSON Schema files for every public model into OUT_DIR."""
    state = _state(ctx)
    paths = export_json_schemas(out_dir)
    _emit_paths(state, paths)


# --- human-readable renderers ------------------------------------------------------------------


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    if not rows:
        return "(none)"
    widths = [max(len(headers[i]), *(len(row[i]) for row in rows)) for i in range(len(headers))]
    lines = ["  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True))]
    for row in rows:
        lines.append("  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)))
    return "\n".join(lines)


def _render_summary(s: api.AssetSummary) -> str:
    return (
        f"{s.asset_id} ({s.asset_type}): {s.frame_count} frame(s), spec_hash={s.spec_hash}\n"
        f"  spec: {s.spec_path}\n"
        f"  directions: {', '.join(s.directions) or '-'}\n"
        f"  animations: {', '.join(s.animations) or '-'}"
    )


def _render_asset_table(summaries: Sequence[api.AssetSummary]) -> str:
    rows = [
        (
            s.asset_id,
            s.asset_type,
            ",".join(s.directions) or "-",
            ",".join(s.animations) or "-",
            str(s.frame_count),
        )
        for s in summaries
    ]
    return _table(("id", "type", "directions", "animations", "frames"), rows)


def _render_inspection(i: api.AssetInspection) -> str:
    lines = [
        f"{i.asset_id} ({i.asset_type}): {i.spec_path}",
        f"  spec_hash: {i.spec_hash}",
        f"  directions: {', '.join(i.directions) or '-'}",
        f"  frame_count: {i.frame_count}",
        f"  palette_size: {i.palette_size}",
        f"  revisions: {i.revision_count} (head={i.head_revision or '-'})",
    ]
    if i.animations:
        lines.append("  animations:")
        for name, info in i.animations.items():
            lines.append(f"    {name}: {info.frame_count} frame(s), loop={info.loop}")
    if i.regions:
        lines.append("  regions:")
        for name, region in i.regions.items():
            lines.append(f"    {name}: anchor={region.anchor} layer={region.layer}")
    if i.output_paths:
        lines.append("  outputs:")
        for name, path in i.output_paths.items():
            lines.append(f"    {name}: {path}")
    return "\n".join(lines)


def _render_render_result(r: api.RenderResult) -> str:
    status = "skipped (cached)" if r.skipped else ("dry-run" if r.dry_run else "rendered")
    lines = [f"{r.asset_id}: {status}, {r.frames_written} frame(s) written"]
    if r.sheet_path:
        lines.append(f"  sheet: {r.sheet_path}")
    if r.contact_sheet_path:
        lines.append(f"  contact_sheet: {r.contact_sheet_path}")
    return "\n".join(lines)


def _render_preview_result(p: api.PreviewResult) -> str:
    lines = [f"{p.asset_id}: format={p.format}" + (" (dry-run)" if p.dry_run else "")]
    for key, path in p.preview_paths.items():
        lines.append(f"  {key}: {path}")
    return "\n".join(lines)


def _render_godot_manifest(m: GodotManifest) -> str:
    lines = [f"{m.asset_id} ({m.asset_type}): spec_hash={m.spec_hash}"]
    for name, path in m.textures.items():
        lines.append(f"  texture[{name}]: {path}")
    if m.sprite_frames:
        lines.append(f"  animations: {', '.join(sorted(m.sprite_frames))}")
    if m.tileset is not None:
        lines.append(f"  tileset: {len(m.tileset.tiles)} tile(s)")
    return "\n".join(lines)


def _render_revision_record(r: RevisionRecord) -> str:
    lines = [f"{r.revision_id}: {r.operation.name} on {r.asset_id} at {r.timestamp}"]
    lines.append(f"  hash: {r.hash_before} -> {r.hash_after}")
    if r.affected_regions:
        lines.append(f"  regions: {', '.join(r.affected_regions)}")
    if r.affected_directions:
        lines.append(f"  directions: {', '.join(r.affected_directions)}")
    if r.affected_frames:
        lines.append(f"  frames: {', '.join(str(f) for f in r.affected_frames)}")
    return "\n".join(lines)


def _render_operations_table(ops: Sequence[api.OperationInfo]) -> str:
    rows = [(op.name, ", ".join(op.params) or "-", op.description) for op in ops]
    return _table(("operation", "params", "description"), rows)


def _render_revisions_table(records: Sequence[RevisionRecord]) -> str:
    rows = [
        (r.revision_id, r.timestamp, r.operation.name, r.parent_revision or "-") for r in records
    ]
    return _table(("revision", "timestamp", "operation", "parent"), rows)


def _render_diff(d: RevisionDiff) -> str:
    lines = [f"{d.revision_a} -> {d.revision_b}: {len(d.operations)} operation(s)"]
    for op in d.operations:
        lines.append(f"  {op.name} {op.params}")
    if d.affected_regions:
        lines.append(f"  regions: {', '.join(d.affected_regions)}")
    if d.affected_directions:
        lines.append(f"  directions: {', '.join(d.affected_directions)}")
    if d.affected_frames:
        lines.append(f"  frames: {', '.join(str(f) for f in d.affected_frames)}")
    return "\n".join(lines)


def _render_seam_report(r: api.SeamReport) -> str:
    lines = [f"{r.asset_id}: worst mismatch {r.worst_mismatch}px"]
    for entry in r.results:
        lines.append(f"  {entry.tile_a}-{entry.tile_b} [{entry.edge}]: {entry.mismatched_pixels}px")
    return "\n".join(lines)


def _render_manifest(m: AssetManifest) -> str:
    vs = m.validation_summary
    lines = [f"{m.asset_id} ({m.asset_type}): spec_hash={m.spec_hash}"]
    for name, path in m.output_paths.items():
        lines.append(f"  {name}: {path}")
    lines.append(f"  validation: {vs.error_count} error(s), {vs.warning_count} warning(s)")
    return "\n".join(lines)


def _render_build_report(r: api.BuildReport) -> str:
    lines = [f"built {len(r.assets)} asset(s), {r.total_findings} finding(s) total"]
    for m in r.assets:
        status = "BLOCKED" if m.validation_summary.blocking else "ok"
        lines.append(f"  {m.asset_id}: {status}")
    if r.failed:
        lines.append(f"failed: {', '.join(r.failed)}")
    return "\n".join(lines)


def _render_style_profile(p: StyleProfile) -> str:
    fields = p.model_dump(mode="json", exclude={"provenance", "schema_version"})
    lines = [f"{k}: {v}" for k, v in fields.items() if v]
    plural = "y" if len(p.provenance) == 1 else "ies"
    lines.append(f"provenance: {len(p.provenance)} entr{plural}")
    return "\n".join(lines)
