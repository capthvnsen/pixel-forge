"""Service layer: the only entry point the CLI and MCP server call.

Every function here returns a pydantic model, never prints, never calls `sys.exit`,
and never reads the clock — callers pass `timestamp` in explicitly. This module wires
together `schemas`, `domain`, `animation`, `rendering`, `validation`, `preview`,
`revisions`, `references`, and `exporters.godot` (all owned by other agents and never
modified here) and owns nothing but the glue and the result models below.

Manifest caching: `render_asset` and `build_asset` each compare `content_hash(doc)`
against the `spec_hash` recorded in `build/<asset_id>/manifest.json` to decide whether
to skip work. Both write that same file, but `build_asset` additionally stamps an
`"godot"` key into `output_paths` once export has run — that's how `build_asset`'s
own skip check tells a *complete* prior build apart from a manifest a bare
`render_asset` call left behind (which has no preview/export data yet).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from pixel_forge import templates
from pixel_forge.animation import ResolvedFrame, resolve_frames, resolve_terrain_frames
from pixel_forge.domain import (
    Project,
    content_hash,
    load_yaml,
    resolve_palette,
    short,
    validate_asset_id,
)
from pixel_forge.domain.paths import CONFIG_FILENAME
from pixel_forge.errors import AssetNotFoundError, ExportError, ForgeError
from pixel_forge.exporters.godot import build_godot_manifest, write_godot_manifest
from pixel_forge.preview import write_preview
from pixel_forge.references import (
    create_profile,
    load_profile,
    scaffold_references,
    update_profile,
)
from pixel_forge.rendering import (
    Canvas,
    SheetCell,
    build_atlas,
    build_contact_sheet,
    build_seam_map,
    build_sprite_sheet,
    check_seams,
    render_asset_frames,
    render_terrain_tiles,
)
from pixel_forge.revisions import (
    affected_targets,
    apply_operation,
    available_operations,
    compare_revisions,
    head_revision,
    load_revisions,
    record_revision,
)
from pixel_forge.schemas import (
    AssetDocUnion,
    AssetManifest,
    AssetType,
    CharacterAsset,
    EnemyAsset,
    GodotManifest,
    OperationSpec,
    ProjectConfig,
    PropAsset,
    ProvenanceEntry,
    RevisionDiff,
    RevisionRecord,
    SheetCellManifest,
    SheetManifest,
    StyleProfile,
    ValidationReport,
    ValidationSummary,
    parse_asset_doc,
)
from pixel_forge.schemas.asset import TerrainAsset
from pixel_forge.validation import RuleContext, run_validation

SpriteDoc = CharacterAsset | EnemyAsset | PropAsset
_GODOT_OUTPUT_KEY = "godot"


# --- result models (pydantic, extra="forbid", JSON-serialisable) --------------------------


class AssetSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    asset_type: AssetType
    spec_path: str
    animations: list[str]
    directions: list[str]
    frame_count: int
    spec_hash: str


class RenderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    spec_hash: str
    frames_written: int
    sheet_path: str | None
    contact_sheet_path: str | None
    frame_paths: list[str]
    skipped: bool
    dry_run: bool


class PreviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    preview_paths: dict[str, str]
    format: Literal["gif", "webp"]
    dry_run: bool


class SeamEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tile_a: str
    tile_b: str
    edge: Literal["N", "S", "E", "W"]
    mismatched_pixels: int


class SeamReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    results: list[SeamEntry]
    seam_map_path: str | None
    worst_mismatch: int


class BuildReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assets: list[AssetManifest]
    blocking: bool
    failed: list[str]
    total_findings: int


class AnimationInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    loop: bool
    frame_count: int
    total_duration_ms: int
    events: list[list[str]]


class RegionInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchor: str
    layer: int
    shape_count: int
    protected: bool


class AssetInspection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    asset_type: AssetType
    spec_path: str
    animations: dict[str, AnimationInfo]
    directions: list[str]
    frame_count: int
    spec_hash: str
    anchors: dict[str, tuple[int, int]]
    regions: dict[str, RegionInfo]
    palette_size: int
    revision_count: int
    head_revision: str | None
    output_paths: dict[str, str]


class OperationInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    params: list[str]


# --- internal helpers -----------------------------------------------------------------------


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _project(root: Path) -> Project:
    return Project.load(root)


def _load_doc(project: Project, asset_id: str) -> AssetDocUnion:
    """Load `asset_id`, raising `PathSecurityError` for a hostile id (via
    `validate_asset_id`, checked before touching the filesystem) or
    `AssetNotFoundError` listing every known id."""
    validate_asset_id(asset_id)
    available = project.discover_assets()
    if asset_id not in available:
        raise AssetNotFoundError(
            f"no asset {asset_id!r} in project at {project.root}; available: {available}"
        )
    return project.load_asset(asset_id)


def _summary(project: Project, doc: AssetDocUnion) -> AssetSummary:
    spec_path = _rel(project.root, project.paths.asset_spec(doc.asset.id))
    if isinstance(doc, TerrainAsset):
        animations = list(doc.animated_tiles)
        directions: list[str] = []
        frame_count = len(resolve_terrain_frames(doc))
    else:
        animations = list(doc.animations)
        directions = list(doc.directions)
        frame_count = len(resolve_frames(doc))
    return AssetSummary(
        asset_id=doc.asset.id,
        asset_type=doc.asset.type,
        spec_path=spec_path,
        animations=animations,
        directions=directions,
        frame_count=frame_count,
        spec_hash=content_hash(doc),
    )


def _validation_report(
    doc: AssetDocUnion,
    frames: Mapping[tuple[str, str, int], Canvas],
    tiles: Mapping[str, Canvas],
) -> ValidationReport:
    palette = resolve_palette(doc.palette)
    resolved: Sequence[ResolvedFrame] = [] if isinstance(doc, TerrainAsset) else resolve_frames(doc)
    ctx = RuleContext(doc=doc, palette=palette, frames=frames, resolved=resolved, tiles=tiles)
    return run_validation(ctx)


def _validation_summary(report: ValidationReport) -> ValidationSummary:
    return ValidationSummary(
        blocking=report.blocking,
        error_count=report.error_count,
        warning_count=report.warning_count,
        finding_count=len(report.findings),
    )


def _existing_manifest(build_dir: Path) -> AssetManifest | None:
    manifest_path = build_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        return AssetManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        # ponytail: a foreign/corrupt manifest.json is treated as "no cache", not an error.
        return None


def _write_manifest(manifest: AssetManifest, build_dir: Path) -> Path:
    build_dir.mkdir(parents=True, exist_ok=True)
    path = build_dir / "manifest.json"
    path.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


# --- project lifecycle -----------------------------------------------------------------------


def init_project(root: Path, name: str, *, dry_run: bool = False) -> ProjectConfig:
    if not dry_run:
        return Project.create(root, name).config
    config_path = root / CONFIG_FILENAME
    if config_path.is_file():
        return ProjectConfig.model_validate(load_yaml(config_path))
    return ProjectConfig(name=name)


def new_asset(
    root: Path, asset_type: AssetType, asset_id: str, *, dry_run: bool = False
) -> AssetSummary:
    project = _project(root)
    validate_asset_id(asset_id)
    if asset_id in project.discover_assets():
        raise ForgeError(f"asset {asset_id!r} already exists in project at {project.root}")
    doc = parse_asset_doc(templates.asset_template(asset_type, asset_id))
    if not dry_run:
        project.save_asset(doc)
    return _summary(project, doc)


def list_assets(root: Path) -> list[AssetSummary]:
    project = _project(root)
    return [_summary(project, project.load_asset(aid)) for aid in project.discover_assets()]


def get_asset(root: Path, asset_id: str) -> AssetDocUnion:
    project = _project(root)
    return _load_doc(project, asset_id)


# --- rendering ---------------------------------------------------------------------------------


def _cells_manifest(cells: Sequence[SheetCell]) -> list[SheetCellManifest]:
    return [
        SheetCellManifest(
            direction=c.direction, animation=c.animation, index=c.index, x=c.x, y=c.y, w=c.w, h=c.h
        )
        for c in cells
    ]


def _finish_render(
    build_dir: Path,
    doc: AssetDocUnion,
    *,
    spec_hash: str,
    output_paths: dict[str, str],
    sheet_manifest: SheetManifest,
    report: ValidationReport,
    sheet_path: str | None,
    contact_sheet_path: str | None,
    frame_paths: list[str],
    frames_written: int,
) -> RenderResult:
    manifest = AssetManifest(
        asset_id=doc.asset.id,
        asset_type=doc.asset.type,
        spec_hash=spec_hash,
        output_paths=output_paths,
        sheet=sheet_manifest,
        preview_paths={},
        validation_summary=_validation_summary(report),
    )
    _write_manifest(manifest, build_dir)
    return RenderResult(
        asset_id=doc.asset.id,
        spec_hash=spec_hash,
        frames_written=frames_written,
        sheet_path=sheet_path,
        contact_sheet_path=contact_sheet_path,
        frame_paths=frame_paths,
        skipped=False,
        dry_run=False,
    )


def _terrain_atlas_rows(doc: TerrainAsset) -> list[list[str]] | None:
    """Explicit `build_atlas` row layout for `doc`'s tiles: each animated tile gets its
    own row, frames contiguous from column 0 -- so Godot's animation-strip contiguity
    requirement holds structurally rather than by accident of sorted-key packing. Static
    tiles (everything not itself an animated tile's frame) fill one more row, sorted.
    `None` (the default sorted-key atlas) when `doc` has no animated tiles, so nothing
    changes for terrain assets without animated water/lava/etc tiles."""
    if not doc.animated_tiles:
        return None
    frame_ids = {tile_id for spec in doc.animated_tiles.values() for tile_id in spec.frames}
    rows = [list(doc.animated_tiles[name].frames) for name in sorted(doc.animated_tiles)]
    static_ids = sorted(tile_id for tile_id in doc.tiles if tile_id not in frame_ids)
    if static_ids:
        rows.append(static_ids)
    return rows


def _render_terrain(
    project: Project, doc: TerrainAsset, *, force: bool, dry_run: bool
) -> RenderResult:
    build_dir = project.paths.build_asset_dir(doc.asset.id)
    spec_hash = content_hash(doc)
    sheet_path = _rel(project.root, build_dir / f"{doc.asset.id}_atlas.png")
    expected_count = len(doc.tiles)

    existing = _existing_manifest(build_dir)
    if not force and existing is not None and existing.spec_hash == spec_hash:
        return RenderResult(
            asset_id=doc.asset.id,
            spec_hash=spec_hash,
            frames_written=0,
            sheet_path=sheet_path,
            contact_sheet_path=None,
            frame_paths=[],
            skipped=True,
            dry_run=dry_run,
        )
    if dry_run:
        return RenderResult(
            asset_id=doc.asset.id,
            spec_hash=spec_hash,
            frames_written=expected_count,
            sheet_path=sheet_path,
            contact_sheet_path=None,
            frame_paths=[],
            skipped=False,
            dry_run=True,
        )

    build_dir.mkdir(parents=True, exist_ok=True)
    tile_canvases = render_terrain_tiles(doc)
    atlas, atlas_cells = build_atlas(tile_canvases, rows=_terrain_atlas_rows(doc))
    atlas.save_png(project.root / sheet_path)
    tile_size = next(iter(doc.tiles.values())).size
    tw, th = tile_size
    sheet_manifest = SheetManifest(
        image_path=sheet_path,
        columns=atlas.width // tw,
        rows=atlas.height // th,
        cell_size=tile_size,
        cells=_cells_manifest(list(atlas_cells.values())),
    )
    report = _validation_report(doc, {}, tile_canvases)

    return _finish_render(
        build_dir,
        doc,
        spec_hash=spec_hash,
        output_paths={"atlas": sheet_path},
        sheet_manifest=sheet_manifest,
        report=report,
        sheet_path=sheet_path,
        contact_sheet_path=None,
        frame_paths=[],
        frames_written=len(tile_canvases),
    )


def _render_sprite(project: Project, doc: SpriteDoc, *, force: bool, dry_run: bool) -> RenderResult:
    build_dir = project.paths.build_asset_dir(doc.asset.id)
    spec_hash = content_hash(doc)
    resolved = resolve_frames(doc)
    frames_dir = build_dir / "frames"
    frame_paths = [
        _rel(project.root, frames_dir / f"{f.animation}_{f.direction}_{f.index}.png")
        for f in resolved
    ]
    sheet_path = _rel(project.root, build_dir / f"{doc.asset.id}_sheet.png")
    contact_sheet_path = _rel(project.root, build_dir / f"{doc.asset.id}_contact.png")

    existing = _existing_manifest(build_dir)
    if not force and existing is not None and existing.spec_hash == spec_hash:
        return RenderResult(
            asset_id=doc.asset.id,
            spec_hash=spec_hash,
            frames_written=0,
            sheet_path=sheet_path,
            contact_sheet_path=contact_sheet_path,
            frame_paths=frame_paths,
            skipped=True,
            dry_run=dry_run,
        )
    if dry_run:
        return RenderResult(
            asset_id=doc.asset.id,
            spec_hash=spec_hash,
            frames_written=len(resolved),
            sheet_path=sheet_path,
            contact_sheet_path=contact_sheet_path,
            frame_paths=frame_paths,
            skipped=False,
            dry_run=True,
        )

    build_dir.mkdir(parents=True, exist_ok=True)
    frames = render_asset_frames(doc)
    frames_dir.mkdir(parents=True, exist_ok=True)
    for f in resolved:
        canvas = frames[(f.animation, f.direction, f.index)]
        canvas.save_png(frames_dir / f"{f.animation}_{f.direction}_{f.index}.png")
    sheet = build_sprite_sheet(
        [(f, frames[(f.animation, f.direction, f.index)]) for f in resolved],
        doc.asset.canvas,
        columns=doc.export.sheet_columns,
    )
    sheet.image.save_png(project.root / sheet_path)
    build_contact_sheet(sheet).save_png(project.root / contact_sheet_path)
    sheet_manifest = SheetManifest(
        image_path=sheet_path,
        columns=sheet.columns,
        rows=sheet.rows,
        cell_size=doc.asset.canvas,
        cells=_cells_manifest(list(sheet.cells)),
    )
    report = _validation_report(doc, frames, {})

    return _finish_render(
        build_dir,
        doc,
        spec_hash=spec_hash,
        output_paths={"sheet": sheet_path, "contact_sheet": contact_sheet_path},
        sheet_manifest=sheet_manifest,
        report=report,
        sheet_path=sheet_path,
        contact_sheet_path=contact_sheet_path,
        frame_paths=frame_paths,
        frames_written=len(frames),
    )


def _render(project: Project, doc: AssetDocUnion, *, force: bool, dry_run: bool) -> RenderResult:
    if isinstance(doc, TerrainAsset):
        return _render_terrain(project, doc, force=force, dry_run=dry_run)
    return _render_sprite(project, doc, force=force, dry_run=dry_run)


def render_asset(
    root: Path, asset_id: str, *, force: bool = False, dry_run: bool = False
) -> RenderResult:
    project = _project(root)
    doc = _load_doc(project, asset_id)
    return _render(project, doc, force=force, dry_run=dry_run)


def validate_asset(root: Path, asset_id: str) -> ValidationReport:
    project = _project(root)
    doc = _load_doc(project, asset_id)
    if isinstance(doc, TerrainAsset):
        return _validation_report(doc, {}, render_terrain_tiles(doc))
    return _validation_report(doc, render_asset_frames(doc), {})


def generate_preview(
    root: Path,
    asset_id: str,
    *,
    fmt: Literal["gif", "webp"] | None = None,
    scale: int = 1,
    dry_run: bool = False,
) -> PreviewResult:
    project = _project(root)
    doc = _load_doc(project, asset_id)
    if isinstance(doc, TerrainAsset):
        raise ForgeError(
            f"asset {asset_id!r} is a terrain asset; preview generation applies only to "
            "character, enemy, and prop assets"
        )
    resolved_fmt = fmt or doc.export.preview_format
    build_dir = project.paths.build_asset_dir(asset_id)
    frames = render_asset_frames(doc)

    all_frames = resolve_frames(doc)
    preview_paths: dict[str, str] = {}
    for animation, anim_spec in doc.animations.items():
        for direction in doc.directions:
            anim_frames = sorted(
                (f for f in all_frames if f.animation == animation and f.direction == direction),
                key=lambda f: f.index,
            )
            canvases = [frames[(f.animation, f.direction, f.index)] for f in anim_frames]
            durations = [f.duration_ms for f in anim_frames]
            key = f"{animation}_{direction}"
            out_base = build_dir / f"preview_{key}"
            preview_paths[key] = _rel(project.root, out_base.with_suffix(f".{resolved_fmt}"))
            if not dry_run:
                build_dir.mkdir(parents=True, exist_ok=True)
                write_preview(
                    out_base,
                    canvases,
                    durations,
                    fmt=resolved_fmt,
                    loop=anim_spec.loop,
                    scale=scale,
                )

    return PreviewResult(
        asset_id=asset_id, preview_paths=preview_paths, format=resolved_fmt, dry_run=dry_run
    )


def _require_texture_on_disk(project: Project, rel_path: str, asset_id: str) -> None:
    if not (project.root / rel_path).is_file():
        raise ExportError(
            f"texture {rel_path!r} for asset {asset_id!r} does not exist yet; "
            f"run render_asset({asset_id!r}) (or force=True if it's stale) before export_godot"
        )


def export_godot(root: Path, asset_id: str, *, dry_run: bool = False) -> GodotManifest:
    project = _project(root)
    doc = _load_doc(project, asset_id)
    spec_hash = content_hash(doc)
    build_dir = project.paths.build_asset_dir(asset_id)
    godot_dir = project.paths.build_godot_dir()

    if isinstance(doc, TerrainAsset):
        tiles = render_terrain_tiles(doc)
        _, atlas_cells = build_atlas(tiles, rows=_terrain_atlas_rows(doc))
        atlas_rel = _rel(project.root, build_dir / f"{asset_id}_atlas.png")
        _require_texture_on_disk(project, atlas_rel, asset_id)
        manifest = build_godot_manifest(
            doc, texture_paths={"atlas": atlas_rel}, spec_hash=spec_hash, atlas_cells=atlas_cells
        )
    else:
        frames = render_asset_frames(doc)
        resolved = resolve_frames(doc)
        sheet = build_sprite_sheet(
            [(f, frames[(f.animation, f.direction, f.index)]) for f in resolved],
            doc.asset.canvas,
            columns=doc.export.sheet_columns,
        )
        sheet_rel = _rel(project.root, build_dir / f"{asset_id}_sheet.png")
        _require_texture_on_disk(project, sheet_rel, asset_id)
        manifest = build_godot_manifest(
            doc,
            sheet=sheet,
            texture_paths={"sheet": sheet_rel},
            spec_hash=spec_hash,
            frames=resolved,
        )

    if not dry_run:
        write_godot_manifest(manifest, godot_dir)
    return manifest


# --- revisions -----------------------------------------------------------------------------------


def apply_asset_operation(
    root: Path, asset_id: str, op: OperationSpec, *, timestamp: str, dry_run: bool = False
) -> RevisionRecord:
    project = _project(root)
    doc_before = _load_doc(project, asset_id)
    doc_after, inverse = apply_operation(doc_before, op)

    if isinstance(doc_after, TerrainAsset):
        report = _validation_report(doc_after, {}, render_terrain_tiles(doc_after))
    else:
        report = _validation_report(doc_after, render_asset_frames(doc_after), {})

    if dry_run:
        parent = head_revision(project.paths, asset_id)
        parent_id = parent.revision_id if parent is not None else None
        hash_before = content_hash(doc_before)
        hash_after = content_hash(doc_after)
        revision_id = short(
            content_hash(
                {
                    "parent": parent_id,
                    "operation": op.model_dump(mode="json"),
                    "hash_after": hash_after,
                }
            )
        )
        affected = affected_targets(doc_before, op)
        return RevisionRecord(
            revision_id=revision_id,
            parent_revision=parent_id,
            timestamp=timestamp,
            operation=op,
            inverse=inverse,
            asset_id=asset_id,
            affected_regions=affected.get("regions", []),
            affected_frames=[int(f) for f in affected.get("frames", [])],
            affected_directions=affected.get("directions", []),
            hash_before=hash_before,
            hash_after=hash_after,
            validation=report,
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


def update_asset_spec(
    root: Path,
    asset_id: str,
    spec: Mapping[str, Any],
    *,
    timestamp: str,
    dry_run: bool = False,
) -> RevisionRecord:
    """Replace an asset's entire spec document in one shot, recorded as a
    `replace_spec` revision (see `revisions.operations`), for structural edits the
    named operations in `apply_asset_operation` don't cover."""
    op = OperationSpec(name="replace_spec", params={"spec": dict(spec)})
    return apply_asset_operation(root, asset_id, op, timestamp=timestamp, dry_run=dry_run)


def compare_asset_revisions(root: Path, asset_id: str, rev_a: str, rev_b: str) -> RevisionDiff:
    project = _project(root)
    _load_doc(project, asset_id)
    return compare_revisions(project.paths, asset_id, rev_a, rev_b)


def list_asset_revisions(root: Path, asset_id: str) -> list[RevisionRecord]:
    project = _project(root)
    _load_doc(project, asset_id)
    return load_revisions(project.paths, asset_id)


def list_operations() -> list[OperationInfo]:
    return [
        OperationInfo(name=op.name, description=op.description, params=list(op.params))
        for op in available_operations()
    ]


# --- inspection / seams --------------------------------------------------------------------------


def inspect_asset(root: Path, asset_id: str) -> AssetInspection:
    project = _project(root)
    doc = _load_doc(project, asset_id)
    summary = _summary(project, doc)
    existing = _existing_manifest(project.paths.build_asset_dir(asset_id))
    output_paths = existing.output_paths if existing is not None else {}
    revisions = load_revisions(project.paths, asset_id)
    head = revisions[-1].revision_id if revisions else None

    if isinstance(doc, TerrainAsset):
        anchors: dict[str, tuple[int, int]] = {}
        regions: dict[str, RegionInfo] = {}
        animations = {
            name: AnimationInfo(
                loop=spec.loop,
                frame_count=len(spec.frames),
                total_duration_ms=spec.frame_duration_ms * len(spec.frames),
                events=[],
            )
            for name, spec in doc.animated_tiles.items()
        }
    else:
        anchors = dict(doc.anchors)
        regions = {
            name: RegionInfo(
                anchor=r.anchor, layer=r.layer, shape_count=len(r.shapes), protected=r.protected
            )
            for name, r in doc.regions.items()
        }
        animations = {
            name: AnimationInfo(
                loop=spec.loop,
                frame_count=len(spec.frames),
                total_duration_ms=sum(f.duration_ms for f in spec.frames),
                events=[list(f.events) for f in spec.frames],
            )
            for name, spec in doc.animations.items()
        }

    return AssetInspection(
        asset_id=summary.asset_id,
        asset_type=summary.asset_type,
        spec_path=summary.spec_path,
        animations=animations,
        directions=summary.directions,
        frame_count=summary.frame_count,
        spec_hash=summary.spec_hash,
        anchors=anchors,
        regions=regions,
        palette_size=len(doc.palette.colors),
        revision_count=len(revisions),
        head_revision=head,
        output_paths=output_paths,
    )


def test_seams(root: Path, asset_id: str) -> SeamReport:
    project = _project(root)
    doc = _load_doc(project, asset_id)
    if not isinstance(doc, TerrainAsset):
        raise ForgeError(
            f"asset {asset_id!r} is not a terrain asset; seam testing applies to terrain tiles only"
        )
    tiles = render_terrain_tiles(doc)
    results = check_seams(tiles)

    seam_map_path: str | None = None
    if doc.tiles:
        build_dir = project.paths.build_asset_dir(asset_id)
        build_dir.mkdir(parents=True, exist_ok=True)
        seam_path = build_dir / f"{asset_id}_seams.png"
        build_seam_map(tiles, [sorted(doc.tiles)]).save_png(seam_path)
        seam_map_path = _rel(project.root, seam_path)

    worst = max((r.mismatched_pixels for r in results), default=0)
    return SeamReport(
        asset_id=asset_id,
        results=[
            SeamEntry(
                tile_a=r.tile_a,
                tile_b=r.tile_b,
                edge=r.edge,
                mismatched_pixels=r.mismatched_pixels,
            )
            for r in results
        ],
        seam_map_path=seam_map_path,
        worst_mismatch=worst,
    )


# --- build -------------------------------------------------------------------------------------


def build_asset(
    root: Path, asset_id: str, *, force: bool = False, timestamp: str | None = None
) -> AssetManifest:
    project = _project(root)
    doc = _load_doc(project, asset_id)
    build_dir = project.paths.build_asset_dir(asset_id)
    spec_hash = content_hash(doc)

    cached = _existing_manifest(build_dir)
    if (
        not force
        and cached is not None
        and cached.spec_hash == spec_hash
        and _GODOT_OUTPUT_KEY in cached.output_paths
    ):
        return cached

    _render(project, doc, force=True, dry_run=False)
    rendered = _existing_manifest(build_dir)
    if rendered is None:
        raise ForgeError(f"internal error: render_asset did not write a manifest for {asset_id!r}")

    preview_paths: dict[str, str] = {}
    if not isinstance(doc, TerrainAsset):
        preview_paths = generate_preview(project.root, asset_id).preview_paths

    export_godot(project.root, asset_id)
    godot_rel = _rel(project.root, project.paths.build_godot_dir() / f"{asset_id}.forge.json")

    manifest = AssetManifest(
        asset_id=doc.asset.id,
        asset_type=doc.asset.type,
        spec_hash=spec_hash,
        output_paths={**rendered.output_paths, _GODOT_OUTPUT_KEY: godot_rel},
        sheet=rendered.sheet,
        preview_paths=preview_paths,
        validation_summary=rendered.validation_summary,
    )
    _write_manifest(manifest, build_dir)
    return manifest


def build_all(root: Path, *, force: bool = False) -> BuildReport:
    project = _project(root)
    manifests: list[AssetManifest] = []
    failed: list[str] = []
    for asset_id in project.discover_assets():
        try:
            manifest = build_asset(root, asset_id, force=force)
        except ForgeError:
            failed.append(asset_id)
            continue
        manifests.append(manifest)
        if manifest.validation_summary.blocking:
            failed.append(asset_id)
    return BuildReport(
        assets=manifests,
        blocking=bool(failed),
        failed=failed,
        total_findings=sum(m.validation_summary.finding_count for m in manifests),
    )


# --- references / style profile ------------------------------------------------------------------


def scaffold_project_references(root: Path) -> list[Path]:
    return scaffold_references(root)


def get_style_profile(root: Path) -> StyleProfile:
    project = _project(root)
    try:
        return load_profile(project.root)
    except ForgeError:
        create_profile(project.root, StyleProfile())
        return load_profile(project.root)


def set_style_profile(
    root: Path, changes: Mapping[str, Any], provenance: Sequence[ProvenanceEntry] = ()
) -> StyleProfile:
    project = _project(root)
    try:
        load_profile(project.root)
    except ForgeError:
        create_profile(project.root, StyleProfile())
    return update_profile(project.root, changes, provenance=provenance)
