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
from dataclasses import replace as _dc_replace
from pathlib import Path
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray
from PIL import Image
from pydantic import BaseModel, ConfigDict

from pixel_forge import templates
from pixel_forge.animation import ResolvedFrame, resolve_frames, resolve_terrain_frames
from pixel_forge.domain import (
    Project,
    check_palette_limit,
    content_hash,
    load_yaml,
    palette_for_polish,
    resolve_palette,
    rgba_to_hex,
    safe_join,
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
    ExternalFrameBackend,
    RenderBackend,
    SheetCell,
    SpriteSheet,
    build_atlas,
    build_contact_sheet,
    build_seam_map,
    build_sprite_sheet,
    check_seams,
    compute_source_pins,
    expand_terrain_variants,
    render_asset_frames,
    render_terrain_tiles,
    verify_pins,
)
from pixel_forge.rendering.annotate import annotate_frame, build_annotated_contact, upscale_view
from pixel_forge.rendering.ingest import extract_palette, load_image, png_to_bitmap
from pixel_forge.rendering.sheet_import import Layout, SheetImportOptions, slice_sheet
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
    ArtDirection,
    AssetDocUnion,
    AssetManifest,
    AssetType,
    BitmapShape,
    CharacterAsset,
    EnemyAsset,
    GodotManifest,
    OperationSpec,
    Palette,
    PaletteColor,
    ProjectConfig,
    PropAsset,
    ProvenanceEntry,
    QualityReport,
    RevisionDiff,
    RevisionRecord,
    SheetCellManifest,
    SheetManifest,
    StyleProfile,
    ValidationReport,
    ValidationSummary,
    parse_asset_doc,
)
from pixel_forge.schemas.asset import SpriteAssetBase, TerrainAsset
from pixel_forge.validation import RuleContext, run_validation
from pixel_forge.validation.quality import score_report

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


class ImportResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    region: str
    at: tuple[int, int]
    width: int
    height: int
    matched: int
    snapped: dict[str, int]
    unmatched: dict[str, int]
    added_colors: list[str]
    revision: RevisionRecord
    dry_run: bool


class ViewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    path: str
    width: int
    height: int
    scale: int


class SheetImportResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    directions: list[str]
    cells_total: int
    cells_skipped: int
    canvas: int
    baseline: int
    palette_size: int
    frame_paths: list[str]
    dry_run: bool


class ImportLayeredResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    canvas: tuple[int, int]
    spec_path: str
    regions: list[str]
    back_regions: list[str]
    anchors: dict[str, tuple[int, int]]
    palette_id: str
    palette_size: int
    revision: RevisionRecord | None
    warnings: list[str]
    dry_run: bool


# --- internal helpers -----------------------------------------------------------------------


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _art_direction(art_direction: ArtDirection | None, doc: AssetDocUnion) -> ArtDirection | None:
    """Resolve the effective art direction for `doc`.

    An explicit caller override always wins. Otherwise the render-polish pass is
    ON by default — except for docs that opt out via `export.polish: false`
    (imported art, whose pixels must round-trip byte-exact) and for docs with
    an external `source:` block, which render through `ExternalFrameBackend`
    (final authored pixels, never post-processed).
    """
    if art_direction is not None:
        return art_direction
    if not doc.export.polish:
        return None
    if isinstance(doc, SpriteAssetBase) and doc.source is not None:
        return None
    if isinstance(doc, TerrainAsset):
        # Terrain renders flat with material-tinted sel-out edges, never the
        # sprite bevel: the per-tile shading/AO/outline stages would raise
        # every 16px block out of the ground (see ArtDirection.terrain_default).
        return ArtDirection.terrain_default()
    return ArtDirection.default()


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


def _require_sprite_doc(doc: AssetDocUnion, fn_name: str) -> SpriteDoc:
    """Narrow `doc` to a character/enemy/prop asset, raising for terrain (which has no
    top-level `regions`/`anchors` — its regions live per-tile)."""
    if isinstance(doc, TerrainAsset):
        raise ForgeError(
            f"asset {doc.asset.id!r} is a terrain asset; {fn_name} applies only to "
            "character, enemy, and prop assets"
        )
    return doc


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


def _base_frames(
    frames: Mapping[tuple[str, str, int] | tuple[str, str, int, int], Canvas],
) -> dict[tuple[str, str, int], Canvas]:
    """Keep only the authored frames; drop eased sub-frame keys (anim, dir, idx, k).

    Validation rules and sheet packing operate on authored frames — sub-frames are
    interpolation artifacts rendered for easing/hold tracks and must not be judged
    as extra frames (or re-packed into sheets).
    """
    return {key: canvas for key, canvas in frames.items() if len(key) == 3}


def _validation_report(
    project: Project,
    doc: AssetDocUnion,
    frames: Mapping[tuple[str, str, int], Canvas],
    tiles: Mapping[str, Canvas],
    *,
    art_direction: ArtDirection | None = None,
) -> ValidationReport:
    if art_direction is not None:
        # The frames were rendered through the polish pass, which quantizes every
        # pixel it writes onto the palette_for_polish-expanded palette — PIX rules
        # must judge those frames against that SAME expanded palette, or every
        # shaded/outlined pixel reads as an unapproved colour (PIX003/PIX004).
        palette = resolve_palette(palette_for_polish(doc.palette))
        # ANI001 subtracts the contact-shadow band from each frame's bbox to find
        # the sprite's own baseline. The renderer clips the shadow to the canvas
        # (it starts one row below the sprite's ground line and must fit inside
        # `canvas`), so report the number of rows it can *actually* draw — using
        # the declared baseline as the ground line — not the configured count,
        # which over-compensates (and reads as drift) on short canvases.
        shadow_rows = (
            art_direction.ground_shadow_rows
            if art_direction.ground_shadow_enabled and art_direction.ground_shadow_strength > 0
            else 0
        )
        if shadow_rows and doc.asset.baseline_y is not None:
            canvas_h = doc.asset.canvas[1]
            shadow_rows = min(shadow_rows, max(0, canvas_h - (doc.asset.baseline_y + 1)))
        polish_shadow_rows = shadow_rows
    else:
        palette = resolve_palette(doc.palette)
        polish_shadow_rows = 0
    resolved: Sequence[ResolvedFrame] = [] if isinstance(doc, TerrainAsset) else resolve_frames(doc)
    asset_dir = project.paths.asset_dir(doc.asset.id)
    ctx = RuleContext(
        doc=doc,
        palette=palette,
        frames=frames,
        resolved=resolved,
        tiles=tiles,
        asset_dir=asset_dir,
        polish_shadow_rows=polish_shadow_rows,
    )
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


def _terrain_atlas_rows(
    doc: TerrainAsset, cells: Mapping[str, Canvas] | None = None
) -> list[list[str]] | None:
    """Explicit `build_atlas` row layout for `doc`'s tiles: each animated tile gets its
    own row, frames contiguous from column 0 -- so Godot's animation-strip contiguity
    requirement holds structurally rather than by accident of sorted-key packing. Static
    tiles (everything not itself an animated tile's frame) fill one more row, sorted --
    including any generated variation cells (`grass.v1`, ...) when `cells` is the
    variant-expanded atlas map. `None` (the default sorted-key atlas) when `doc` has no
    animated tiles, so nothing changes for terrain assets without animated water/lava/etc
    tiles."""
    if not doc.animated_tiles:
        return None
    frame_ids = {tile_id for spec in doc.animated_tiles.values() for tile_id in spec.frames}
    rows = [list(doc.animated_tiles[name].frames) for name in sorted(doc.animated_tiles)]
    static_ids = sorted(tile_id for tile_id in (cells or doc.tiles) if tile_id not in frame_ids)
    if static_ids:
        rows.append(static_ids)
    return rows


def _terrain_cell_count(doc: TerrainAsset) -> int:
    """How many atlas cells `doc` produces once `TileSpec.variations` are
    expanded: every animation-frame tile is one cell, every static tile is
    `max(1, variations)` cells (the base plus its generated variants)."""
    frame_ids = {tile_id for spec in doc.animated_tiles.values() for tile_id in spec.frames}
    return sum(
        1 if tile_id in frame_ids else max(1, spec.variations)
        for tile_id, spec in doc.tiles.items()
    )


def _terrain_atlas_cells(
    doc: TerrainAsset, tiles: Mapping[str, Canvas], art_direction: ArtDirection | None
) -> dict[str, Canvas]:
    """The atlas cell map for `doc`: base tiles plus the generated variation
    cells each tile's `TileSpec.variations` declares. Variants are expanded
    against the polish-expanded palette when the tiles were polished (so the
    scatter uses each material's own ramp tones) and the declared palette
    otherwise; either way the expansion is deterministic and seam-preserving."""
    palette = (
        resolve_palette(palette_for_polish(doc.palette))
        if art_direction is not None
        else resolve_palette(doc.palette)
    )
    return expand_terrain_variants(doc, tiles, palette)


def _render_terrain(
    project: Project,
    doc: TerrainAsset,
    *,
    force: bool,
    dry_run: bool,
    art_direction: ArtDirection | None,
) -> RenderResult:
    build_dir = project.paths.build_asset_dir(doc.asset.id)
    spec_hash = content_hash(doc)
    sheet_path = _rel(project.root, build_dir / f"{doc.asset.id}_atlas.png")
    expected_count = _terrain_cell_count(doc)

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
    tile_canvases = render_terrain_tiles(doc, art_direction=art_direction)
    atlas_tiles = _terrain_atlas_cells(doc, tile_canvases, art_direction)
    atlas, atlas_cells = build_atlas(atlas_tiles, rows=_terrain_atlas_rows(doc, atlas_tiles))
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
    report = _validation_report(project, doc, {}, tile_canvases, art_direction=art_direction)

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
        frames_written=len(atlas_tiles),
    )


def _cached_build_is_trustworthy(project: Project, doc: AssetDocUnion) -> bool:
    """Whether a manifest cache hit on `spec_hash` can be trusted without re-rendering.

    Always true for shape-DSL and terrain docs. For a `source:` doc, `spec_hash` never
    moves when the art on disk changes without a re-pin, so a cache hit alone would
    serve stale pixels -- true only once `verify_pins` confirms every pinned file still
    matches (it raises the same `RenderError` an actual render would, on a mismatch or
    a missing file). An *unpinned* source doc has nothing to verify against, so it is
    never treated as cacheable.
    """
    if isinstance(doc, TerrainAsset) or doc.source is None:
        return True
    if not doc.source.pins:
        return False
    verify_pins(doc, project.paths.asset_dir(doc.asset.id))
    return True


def _sprite_backend(project: Project, doc: SpriteDoc) -> RenderBackend | None:
    """The backend a sprite doc renders through: external when it pins files, else the
    default shape-DSL one (signalled by None, which `render_asset_frames` resolves)."""
    if doc.source is None:
        return None
    return ExternalFrameBackend(project.paths.asset_dir(doc.asset.id))


def _sprite_sheet(
    project: Project, doc: SpriteDoc, *, art_direction: ArtDirection | None = None
) -> SpriteSheet:
    """Render every frame and pack it into a sprite sheet — the shared setup behind
    `render_annotated_contact` and `render_contact_sheet`."""
    resolved = resolve_frames(doc)
    frames = render_asset_frames(
        doc,
        _sprite_backend(project, doc),
        art_direction=_art_direction(art_direction, doc),
    )
    return build_sprite_sheet(
        [(f, frames[(f.animation, f.direction, f.index)]) for f in resolved],
        doc.asset.canvas,
        columns=doc.export.sheet_columns,
    )


def _render_sprite(
    project: Project,
    doc: SpriteDoc,
    *,
    force: bool,
    dry_run: bool,
    art_direction: ArtDirection | None,
) -> RenderResult:
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
    if (
        not force
        and existing is not None
        and existing.spec_hash == spec_hash
        and _cached_build_is_trustworthy(project, doc)
    ):
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
    frames = render_asset_frames(doc, _sprite_backend(project, doc), art_direction=art_direction)
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
    report = _validation_report(project, doc, _base_frames(frames), {}, art_direction=art_direction)

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


def _render(
    project: Project,
    doc: AssetDocUnion,
    *,
    force: bool,
    dry_run: bool,
    art_direction: ArtDirection | None = None,
) -> RenderResult:
    resolved = _art_direction(art_direction, doc)
    if isinstance(doc, TerrainAsset):
        return _render_terrain(project, doc, force=force, dry_run=dry_run, art_direction=resolved)
    return _render_sprite(project, doc, force=force, dry_run=dry_run, art_direction=resolved)


def render_asset(
    root: Path,
    asset_id: str,
    *,
    force: bool = False,
    dry_run: bool = False,
    art_direction: ArtDirection | None = None,
) -> RenderResult:
    project = _project(root)
    doc = _load_doc(project, asset_id)
    return _render(project, doc, force=force, dry_run=dry_run, art_direction=art_direction)


def validate_asset(
    root: Path, asset_id: str, *, art_direction: ArtDirection | None = None
) -> ValidationReport:
    project = _project(root)
    doc = _load_doc(project, asset_id)
    resolved = _art_direction(art_direction, doc)
    if isinstance(doc, TerrainAsset):
        return _validation_report(
            project,
            doc,
            {},
            render_terrain_tiles(doc, art_direction=resolved),
            art_direction=resolved,
        )
    return _validation_report(
        project,
        doc,
        _base_frames(
            render_asset_frames(doc, _sprite_backend(project, doc), art_direction=resolved)
        ),
        {},
        art_direction=resolved,
    )


def quality_asset(
    root: Path, asset_id: str, *, art_direction: ArtDirection | None = None
) -> QualityReport:
    """Machine-readable quality score + repair feedback for an asset.

    Wraps `validate_asset` and scores the resulting report deterministically via
    `validation.quality.score_report` (a pure function of the report — no
    re-rendering, no rule re-runs). Returns a `QualityReport` with a 0-100
    `score`, a `verdict`, and one `QualityIssue` per finding carrying a machine
    `type`, pixel `coordinates` when the rule localised them, and a
    `suggested_fix` an agent can act on.
    """
    report = validate_asset(root, asset_id, art_direction=art_direction)
    return score_report(report)


def generate_preview(
    root: Path,
    asset_id: str,
    *,
    fmt: Literal["gif", "webp"] | None = None,
    scale: int = 1,
    dry_run: bool = False,
    art_direction: ArtDirection | None = None,
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
    frames = render_asset_frames(
        doc, _sprite_backend(project, doc), art_direction=_art_direction(art_direction, doc)
    )

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


def export_godot(
    root: Path,
    asset_id: str,
    *,
    dry_run: bool = False,
    art_direction: ArtDirection | None = None,
) -> GodotManifest:
    project = _project(root)
    doc = _load_doc(project, asset_id)
    spec_hash = content_hash(doc)
    build_dir = project.paths.build_asset_dir(asset_id)
    godot_dir = project.paths.build_godot_dir()
    direction = _art_direction(art_direction, doc)

    if isinstance(doc, TerrainAsset):
        tiles = render_terrain_tiles(doc, art_direction=direction)
        atlas_tiles = _terrain_atlas_cells(doc, tiles, direction)
        _, atlas_cells = build_atlas(atlas_tiles, rows=_terrain_atlas_rows(doc, atlas_tiles))
        atlas_rel = _rel(project.root, build_dir / f"{asset_id}_atlas.png")
        _require_texture_on_disk(project, atlas_rel, asset_id)
        # Variation cells are visual variety in the atlas texture, not Godot
        # tiles: the manifest describes only the declared tile ids (and
        # animation frames), so `build_tileset` never sees `grass.v1` cells.
        godot_cells = {
            tile_id: cell for tile_id, cell in atlas_cells.items() if tile_id in doc.tiles
        }
        manifest = build_godot_manifest(
            doc, texture_paths={"atlas": atlas_rel}, spec_hash=spec_hash, atlas_cells=godot_cells
        )
    else:
        frames = render_asset_frames(doc, _sprite_backend(project, doc), art_direction=direction)
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
    root: Path,
    asset_id: str,
    op: OperationSpec,
    *,
    timestamp: str,
    dry_run: bool = False,
    art_direction: ArtDirection | None = None,
) -> RevisionRecord:
    project = _project(root)
    doc_before = _load_doc(project, asset_id)
    doc_after, inverse = apply_operation(doc_before, op)
    direction = _art_direction(art_direction, doc_after)

    if isinstance(doc_after, TerrainAsset):
        report = _validation_report(
            project,
            doc_after,
            {},
            render_terrain_tiles(doc_after, art_direction=direction),
            art_direction=direction,
        )
    else:
        report = _validation_report(
            project,
            doc_after,
            _base_frames(
                render_asset_frames(
                    doc_after, _sprite_backend(project, doc_after), art_direction=direction
                )
            ),
            {},
            art_direction=direction,
        )

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
    art_direction: ArtDirection | None = None,
) -> RevisionRecord:
    """Replace an asset's entire spec document in one shot, recorded as a
    `replace_spec` revision (see `revisions.operations`), for structural edits the
    named operations in `apply_asset_operation` don't cover."""
    op = OperationSpec(name="replace_spec", params={"spec": dict(spec)})
    return apply_asset_operation(
        root, asset_id, op, timestamp=timestamp, dry_run=dry_run, art_direction=art_direction
    )


def pin_asset_source(
    root: Path, asset_id: str, *, timestamp: str, dry_run: bool = False
) -> RevisionRecord:
    """Record the sha256 of every file an external-source asset's authored frames
    reference, as a `replace_spec` revision.

    This is the step that makes externally-produced pixels behave like drawn ones: once
    pinned, changing the art on disk changes the document hash, so the render cache
    invalidates, the revision log shows the transition, and a file that changes without
    a re-pin is a render error rather than a silent redefinition."""
    project = _project(root)
    doc = _load_doc(project, asset_id)
    if isinstance(doc, TerrainAsset):
        raise ForgeError(
            f"asset {asset_id!r} is a terrain asset; external frame sources apply only "
            "to character, enemy, and prop assets"
        )
    if doc.source is None:
        raise ForgeError(
            f"asset {asset_id!r} declares no `source:` block, so it has no external "
            "frames to pin; it renders from `regions` via the shape DSL"
        )
    pins = compute_source_pins(doc, project.paths.asset_dir(asset_id))
    spec = doc.model_dump(mode="json", exclude={"kind"})
    spec["source"] = {**spec["source"], "pins": pins}
    return update_asset_spec(root, asset_id, spec, timestamp=timestamp, dry_run=dry_run)


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


# --- bitmap import / vision loop ------------------------------------------------------------------

# Fraction of opaque source pixels that may go unmatched (snap=False, extend_palette=False)
# before import_region refuses outright instead of just reporting them. A handful of
# off-palette edge pixels is a normal reason to reach for --snap; losing more than half the
# art to silent transparency means the palette is simply wrong for this source image, and an
# import that "succeeds" while discarding most of the pixels is worse than a loud failure.
_UNMATCHED_RAISE_FRACTION = 0.5


def _extend_palette(
    palette: Palette, unmatched: Mapping[str, int], limit: int
) -> tuple[Palette, list[str]]:
    """Append one new `PaletteColor` per unmatched hex to `palette`.

    Ordered by descending pixel count then ascending hex (the same tie-break
    `ingest.extract_palette` uses), so the result is a pure function of `unmatched`,
    never of dict iteration order. Ids are `import_<hex-without-#>`: deterministic and
    collision-free, since a hex code is unique per `unmatched` entry.
    """
    ordered = sorted(unmatched.items(), key=lambda kv: (-kv[1], kv[0]))
    existing_ids = {c.id for c in palette.colors}
    new_colors: list[PaletteColor] = []
    new_ids: list[str] = []
    for hex_str, _count in ordered:
        color_id = f"import_{hex_str.lstrip('#')}"
        if color_id in existing_ids:
            raise ForgeError(
                f"import_region: generated palette id {color_id!r} for colour {hex_str} "
                f"already exists in palette {palette.id!r}"
            )
        new_colors.append(PaletteColor(id=color_id, hex=hex_str))
        new_ids.append(color_id)
        existing_ids.add(color_id)
    extended = Palette(id=palette.id, colors=[*palette.colors, *new_colors])
    excess = check_palette_limit(extended, limit)
    if excess:
        raise ForgeError(
            f"import_region: extending the palette by {len(new_colors)} colour(s) would grow "
            f"it to {len(extended.colors)}, exceeding validation.palette_limit of {limit} "
            f"(over by {len(excess)}: {', '.join(excess)})"
        )
    return extended, new_ids


def import_region(
    root: Path,
    asset_id: str,
    region: str,
    png_path: str | Path,
    *,
    direction: str | None = None,
    at: tuple[int, int] | None = None,
    snap: bool = False,
    extend_palette: bool = False,
    replace: bool = True,
    timestamp: str,
    dry_run: bool = False,
) -> ImportResult:
    """Import a PNG's pixels into `region` as a palette-indexed `bitmap` shape.

    The bitmap is positioned relative to the region's anchor: by default at the
    opaque bounding box's trimmed offset (`IngestReport.trimmed_to`), or at `at` when
    given explicitly. `replace=True` (the default) replaces the region's shapes;
    `replace=False` appends the bitmap after them.

    `direction` is not expressible in the current schema: `regions` are shared across
    every direction and `direction_overrides` carries only offset/visibility/
    color_swap/scale_size transforms, never a distinct shape list, so any non-`None`
    value raises rather than silently importing into the shared region.

    With `extend_palette=False` and `snap=False`, source colours that match no
    palette colour are dropped (rendered transparent) and reported in
    `ImportResult.unmatched` by hex — except when they exceed
    `_UNMATCHED_RAISE_FRACTION` of the opaque source pixels, in which case this raises
    instead of silently importing what would be a mostly-empty bitmap.

    Recorded as a `replace_spec` revision via `update_asset_spec` — the generic
    "structural edit" operation already used for `pin_asset_source` — rather than a
    bespoke operation, so imported art goes through the same revision machinery
    (protection checks, validation, the append-only log) as every other edit.
    """
    project = _project(root)
    doc = _require_sprite_doc(_load_doc(project, asset_id), "import_region")
    if direction is not None:
        raise ForgeError(
            "import_region: `direction` is not supported — regions are shared across every "
            "direction in the current schema, and direction_overrides carries only "
            "offset/visibility/color_swap/scale_size, never a distinct shape list. Import "
            "into the shared region with direction=None instead."
        )
    if region not in doc.regions:
        raise ForgeError(f"unknown region {region!r}; available regions: {sorted(doc.regions)}")

    image = load_image(safe_join(project.root, str(png_path)))
    resolved_palette = resolve_palette(doc.palette)
    bitmap, report = png_to_bitmap(image, resolved_palette, snap=snap, trim=True)

    new_palette = doc.palette
    added_colors: list[str] = []
    if extend_palette and report.unmatched:
        new_palette, added_colors = _extend_palette(
            doc.palette, report.unmatched, doc.validation.palette_limit
        )
        resolved_palette = resolve_palette(new_palette)
        bitmap, report = png_to_bitmap(image, resolved_palette, snap=snap, trim=True)
    report = _dc_replace(report, added_colors=tuple(added_colors))

    total_unmatched = sum(report.unmatched.values())
    total_opaque = report.matched + sum(report.snapped.values()) + total_unmatched
    if total_opaque and total_unmatched / total_opaque > _UNMATCHED_RAISE_FRACTION:
        raise ForgeError(
            f"import_region: {total_unmatched}/{total_opaque} opaque pixel(s) "
            f"({total_unmatched / total_opaque:.0%}) match no palette colour; refusing an "
            "import that would silently discard most of the source art. Pass snap=True to "
            "snap to the nearest palette colour, or extend_palette=True to add the missing "
            "colours instead."
        )

    if report.trimmed_to is not None:
        x0, y0, _x1, _y1 = report.trimmed_to
    else:
        x0, y0 = 0, 0
    final_at = at if at is not None else (x0, y0)
    bitmap_shape = BitmapShape.model_validate({**bitmap, "at": list(final_at)})

    spec = doc.model_dump(mode="json", exclude={"kind"})
    if new_palette is not doc.palette:
        spec["palette"] = new_palette.model_dump(mode="json")
    shape_json = bitmap_shape.model_dump(mode="json")
    if replace:
        spec["regions"][region]["shapes"] = [shape_json]
    else:
        spec["regions"][region]["shapes"].append(shape_json)
    # Imported pixels are authored elsewhere and must round-trip byte-exact;
    # the render-polish pass would recolor silhouette edges, so opt out.
    spec.setdefault("export", {})["polish"] = False

    record = update_asset_spec(root, asset_id, spec, timestamp=timestamp, dry_run=dry_run)

    return ImportResult(
        asset_id=asset_id,
        region=region,
        at=final_at,
        width=report.width,
        height=report.height,
        matched=report.matched,
        snapped=dict(report.snapped),
        unmatched=dict(report.unmatched),
        added_colors=list(added_colors),
        revision=record,
        dry_run=dry_run,
    )


def extract_palette_from_png(root: Path, png_path: str | Path, *, max_colors: int = 24) -> Palette:
    """Build a `Palette` from a PNG's most frequent opaque colours (see
    `rendering.ingest.extract_palette`), so externally produced art can seed a
    palette instead of hand-transcribing hex codes. `png_path` is resolved against
    the project root; a path escaping it raises `PathSecurityError`."""
    project = _project(root)
    image = load_image(safe_join(project.root, str(png_path)))
    return extract_palette(image, max_colors=max_colors)


def import_sheet(
    root: Path,
    asset_id: str,
    sheet_path: str | Path,
    *,
    grid: tuple[int, int] | None = None,
    cell: tuple[int, int] | None = None,
    layout: Layout | None = None,
    directions: Sequence[str] | None = None,
    scale: int = 1,
    canvas: int = 48,
    baseline: int = 44,
    background: str = "auto",
    animation: str = "idle",
    frame_duration_ms: int = 200,
    frames_per_cell: int = 1,
    palette_limit: int = 24,
    replace: bool = False,
    dry_run: bool = False,
) -> SheetImportResult:
    """Slice a hand-authored directional grid sheet (a diffusion model's or artist's
    single-image compass layout) into a new `source:`-backed character asset: one
    frame PNG per direction under `assets/<asset_id>/frames/`, cropped and
    baseline-aligned by `pixel_forge.rendering.sheet_import.slice_sheet`, pinned the
    same way `pin_asset_source` pins hand-supplied art.

    Refuses to overwrite an existing asset id unless `replace=True`. `dry_run=True`
    computes and returns the same result without writing anything. `sheet_path` is
    resolved against the project root and must stay inside it.
    """
    project = _project(root)
    validate_asset_id(asset_id)
    if not replace and asset_id in project.discover_assets():
        raise ForgeError(
            f"asset {asset_id!r} already exists in project at {project.root}; pass "
            "replace=True to overwrite it"
        )

    image = load_image(safe_join(project.root, str(sheet_path)))
    options = SheetImportOptions(
        grid=grid,
        cell=cell,
        layout=layout,
        directions=tuple(directions) if directions is not None else None,
        scale=scale,
        canvas=canvas,
        baseline=baseline,
        background=background,
        frames_per_cell=frames_per_cell,
        palette_limit=palette_limit,
    )
    report = slice_sheet(image, options)

    asset_dir = project.paths.asset_dir(asset_id)
    frames_dir_name = "frames"
    frame_names = [(f, f"{animation}_{f.direction}_{f.index}.png") for f in report.frames]
    frame_paths = sorted(
        _rel(project.root, asset_dir / frames_dir_name / name) for _f, name in frame_names
    )

    if dry_run:
        return SheetImportResult(
            asset_id=asset_id,
            directions=list(report.directions),
            cells_total=report.cells_total,
            cells_skipped=report.cells_skipped,
            canvas=canvas,
            baseline=baseline,
            palette_size=len(report.palette.colors),
            frame_paths=frame_paths,
            dry_run=True,
        )

    spec: dict[str, Any] = {
        "schema_version": 1,
        "asset": {
            "id": asset_id,
            "type": "character",
            "canvas": [canvas, canvas],
            "baseline_y": baseline,
        },
        "palette": report.palette.model_dump(mode="json"),
        "directions": list(report.directions),
        "anchors": {"feet": [canvas // 2, baseline]},
        "regions": {},
        "source": {
            "frames_dir": frames_dir_name,
            "pattern": "{animation}_{direction}_{index}.png",
            "pins": {},
        },
        "animations": {
            animation: {
                "loop": True,
                "frames": [{"duration_ms": frame_duration_ms} for _ in range(frames_per_cell)],
            }
        },
        "export": {"polish": False},  # imported sheet art is final; never polish
        "validation": {"palette_limit": palette_limit},
    }

    frames_dir = asset_dir / frames_dir_name
    frames_dir.mkdir(parents=True, exist_ok=True)
    for frame, name in frame_names:
        frame.canvas.save_png(frames_dir / name)

    unpinned_doc = _require_sprite_doc(parse_asset_doc(spec), "import_sheet")
    spec["source"]["pins"] = compute_source_pins(unpinned_doc, asset_dir)
    project.save_asset(parse_asset_doc(spec))

    return SheetImportResult(
        asset_id=asset_id,
        directions=list(report.directions),
        cells_total=report.cells_total,
        cells_skipped=report.cells_skipped,
        canvas=canvas,
        baseline=baseline,
        palette_size=len(report.palette.colors),
        frame_paths=frame_paths,
        dry_run=False,
    )


# --- layered-character import (the sprite factory's front door) ---------------------------------

_REQUIRED_FRONT_LAYERS = frozenset(
    {"torso", "head", "arm_left", "arm_right", "leg_left", "leg_right"}
)
_OPTIONAL_LAYERS = frozenset({"weapon", "hair", "shadow", "face"})

# Region z-order (Region.layer), bottom to top, for the front view. Declaration
# order here is also the draw/iteration order everywhere below. Legs sit under
# the torso (it overlaps the hips), arms over the torso's sides, head/hair/
# weapon on top; the shadow is always the bottommost region.
_FRONT_LAYER_Z: dict[str, int] = {
    "shadow": -10,
    "leg_left": 0,
    "leg_right": 1,
    "torso": 10,
    "arm_left": 20,
    "arm_right": 21,
    "head": 30,
    "face": 35,
    "hair": 40,
    "weapon": 50,
}
# Back view: arms and legs hang BEHIND the torso mass (seen from behind, the
# torso overlaps the inner thighs and the upper arms), so they sit below
# `back_torso` — the reverse of the front ordering. A separate z block keeps
# back regions from interleaving with front ones; they are hidden by default
# (see import_layered) so this ordering only matters once a later piece renders
# the back direction.
_BACK_LAYER_Z: dict[str, int] = {
    "shadow": 90,
    "arm_left": 100,
    "arm_right": 101,
    "leg_left": 110,
    "leg_right": 111,
    "torso": 120,
    "head": 130,
    "face": 135,
    "hair": 140,
    "weapon": 150,
}

# Which anchor each region hangs off. Limbs hang off their joint anchor so
# joint-pivot articulation can rotate a region about its own anchor; `weapon`
# follows the right shoulder by convention (main hand); `shadow` anchored at
# `feet` is how the walk-cycle and pose machinery recognise it as static
# (`animation.cycles._discover_roles`, `revisions.operations`).
_REGION_ANCHOR: dict[str, str] = {
    "torso": "root",
    "head": "head_top",
    "face": "head_top",
    "hair": "head_top",
    "arm_left": "shoulder_left",
    "arm_right": "shoulder_right",
    "leg_left": "hip_left",
    "leg_right": "hip_right",
    "weapon": "shoulder_right",
    "shadow": "feet",
}

# A canvas dimension beyond which the input is almost certainly not a single
# pixel-art sprite (e.g. an unscaled render). Warned about, never refused.
_LAYERED_CANVAS_WARN = 128


def _opaque_bbox(arr: NDArray[np.uint8]) -> tuple[int, int, int, int] | None:
    """Half-open `(x0, y0, x1, y1)` bbox of pixels with alpha >= 128 — the same
    binary opacity threshold `rendering.ingest.png_to_bitmap` imports by, so
    anchors derived from this bbox agree with the imported bitmap exactly."""
    ys, xs = np.nonzero(arr[..., 3] >= 128)
    if xs.size == 0:
        return None
    return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)


def _palette_from_composites(
    composites: Sequence[NDArray[np.uint8]], max_colors: int, palette_id: str
) -> tuple[Palette, int]:
    """Build a `Palette` from the most frequent opaque colours across composites.

    Same deterministic contract as `rendering.ingest.extract_palette`: alpha is
    binary (>= 128 opaque), colours are counted per-hex across every composite,
    ordered by descending pixel count with ties broken by ascending hex string,
    and capped at `max_colors`. Ids are `c00`, `c01`, ... in that order.

    Returns `(palette, unique_colors)` where `unique_colors` is the total number
    of distinct opaque colours found *before* the cap — the caller uses it to
    warn when source art exceeds `max_colors`.
    """
    counts: dict[str, int] = {}
    for arr in composites:
        opaque = arr[..., 3] >= 128
        for r, g, b, _a in arr[opaque].tolist():
            hex_str = rgba_to_hex((r, g, b, 255))
            counts[hex_str] = counts.get(hex_str, 0) + 1
    unique_colors = len(counts)
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:max_colors]
    colors = [
        PaletteColor(id=f"c{i:02d}", hex=hex_str) for i, (hex_str, _count) in enumerate(ordered)
    ]
    return Palette(id=palette_id, colors=colors), unique_colors


def _composite_binary(
    placed: Sequence[tuple[NDArray[np.uint8], int, int]], canvas: tuple[int, int]
) -> NDArray[np.uint8]:
    """Painter's-algorithm composite of RGBA layers (each at a canvas offset).

    Matches the renderer's semantics exactly — binary alpha (>= 128 opaque),
    later layers overwrite earlier ones, out-of-canvas pixels clip — so this is
    the reference the imported spec's rendered frame must equal byte-for-byte.
    """
    width, height = canvas
    out = np.zeros((height, width, 4), dtype=np.uint8)
    for arr, dx, dy in placed:
        img_h, img_w = arr.shape[:2]
        x0, y0 = max(dx, 0), max(dy, 0)
        x1, y1 = min(dx + img_w, width), min(dy + img_h, height)
        if x0 >= x1 or y0 >= y1:
            continue
        src = arr[y0 - dy : y1 - dy, x0 - dx : x1 - dx]
        mask = src[..., 3] >= 128
        target = out[y0:y1, x0:x1]
        target[mask] = src[mask]
        target[..., 3] = np.where(mask, 255, target[..., 3])
    return out


def _bbox_center(lo: int, hi: int) -> int:
    """Centre pixel of the half-open range `[lo, hi)`: the exact midpoint for
    odd widths, the pixel just past it for even widths. Pure integer math."""
    return lo + (hi - lo) // 2


def _layered_anchors(
    bboxes: Mapping[str, tuple[int, int, int, int]], canvas: tuple[int, int]
) -> dict[str, tuple[int, int]]:
    """Derive the anchor map from the front-layer bboxes (canvas coordinates).

    - `feet`: bottom-centre of the union of both leg bboxes. The walk-cycle
      generator and the pose/static machinery require an anchor named `feet`.
    - `hip_left`/`hip_right`: top-centre of each leg bbox — the leg pivot.
    - `shoulder_left`/`shoulder_right`: top-INNER corner of each arm bbox,
      "inner" being the vertical edge nearest the canvas centre line — the arm
      pivot for swing-arm / joint articulation.
    - `head_top`: top-centre of the head bbox.
    - `root`: centre of the torso bbox.
    """
    torso = bboxes["torso"]
    head = bboxes["head"]
    leg_l = bboxes["leg_left"]
    leg_r = bboxes["leg_right"]
    legs = (
        min(leg_l[0], leg_r[0]),
        min(leg_l[1], leg_r[1]),
        max(leg_l[2], leg_r[2]),
        max(leg_l[3], leg_r[3]),
    )
    centre_x = canvas[0] // 2

    def shoulder(name: str) -> tuple[int, int]:
        x0, y0, x1, _y1 = bboxes[name]
        inner_x = x0 if _bbox_center(x0, x1) >= centre_x else x1 - 1
        return (inner_x, y0)

    return {
        "root": (_bbox_center(torso[0], torso[2]), _bbox_center(torso[1], torso[3])),
        "feet": (_bbox_center(legs[0], legs[2]), legs[3] - 1),
        "head_top": (_bbox_center(head[0], head[2]), head[1]),
        "shoulder_left": shoulder("arm_left"),
        "shoulder_right": shoulder("arm_right"),
        "hip_left": (_bbox_center(leg_l[0], leg_l[2]), leg_l[1]),
        "hip_right": (_bbox_center(leg_r[0], leg_r[2]), leg_r[1]),
    }


def import_layered(
    root: Path,
    asset_id: str,
    front_layers: Mapping[str, str | Path],
    *,
    back_layers: Mapping[str, str | Path] | None = None,
    canvas: tuple[int, int] | None = None,
    max_colors: int = 16,
    replace: bool = False,
    timestamp: str,
    dry_run: bool = False,
) -> ImportLayeredResult:
    """Import a layered front-view drawing (one PNG per body part) as a new
    character asset: one `bitmap` region per layer, a derived palette,
    synthesized anchors (including the shoulder/hip joint anchors later pieces
    pivot on), `export.polish: False`, and one `replace_spec` revision.

    `front_layers` must include every required layer (`torso`, `head`,
    `arm_left`, `arm_right`, `leg_left`, `leg_right`) and may include the
    optional `weapon`, `hair`, `shadow`, and `face` (a front-only face-detail
    layer — screen/visor/eyes — that the direction projection strips from
    back-facing views); any other name raises. Region
    names match the conventions `animation.cycles._discover_roles` reads, so
    walk cycles and pose templates work on the imported asset immediately.

    All layer PNGs share one coordinate space (their own pixel coordinates, as
    a drawing app exports them). `canvas=None` (the default) derives the canvas
    from the union of the front layers' opaque extents, shifting content so the
    union's top-left lands at (0, 0); an explicit `canvas` keeps the layers'
    coordinates verbatim and raises if the front union exceeds it.

    The palette is extracted from the composited front (plus the composited
    back when supplied), capped at `max_colors`. Because every visible colour
    comes from the art itself, imports are exact; colours dropped by the cap
    (or by full occlusion) are reported in `ImportLayeredResult.warnings`.

    Back layers, when supplied, are stored as regions named `back_<layer>`
    (same anchors and bitmap treatment, a separate z block — see
    `_BACK_LAYER_Z`) and hidden via `visible: False` transforms on the imported
    `idle` frame: the current schema has no doc-level region visibility or
    per-direction shape lists, so frame transforms are the only in-schema way
    to carry the back view today. Any animation added later must re-hide them
    (or a schema field must take over — see the module docs).

    Every path is resolved against the project root through `safe_join`; a
    layer path escaping the project raises `PathSecurityError`. Imported pixels
    are authored elsewhere, so `export.polish` is forced False: rendering the
    imported spec's south frame reproduces the alpha-composited front layers
    byte-for-byte. `dry_run=True` builds and validates the whole spec in memory
    and writes nothing (no asset, no revision).
    """
    project = _project(root)
    validate_asset_id(asset_id)
    if not replace and asset_id in project.discover_assets():
        raise ForgeError(
            f"asset {asset_id!r} already exists in project at {project.root}; pass "
            "replace=True to overwrite it"
        )
    if max_colors < 1:
        raise ForgeError(f"import_layered: max_colors must be >= 1, got {max_colors}")

    known = _REQUIRED_FRONT_LAYERS | _OPTIONAL_LAYERS
    back_layers = back_layers or {}
    for label, layer_map in (("front", front_layers), ("back", back_layers)):
        unknown = sorted(set(layer_map) - known)
        if unknown:
            raise ForgeError(
                f"import_layered: unknown {label} layer name(s) {unknown}; valid layer "
                f"names: {sorted(known)}"
            )
    missing = sorted(_REQUIRED_FRONT_LAYERS - set(front_layers))
    if missing:
        raise ForgeError(
            f"import_layered: missing required front layer(s) {missing}; the front view "
            f"needs all of {sorted(_REQUIRED_FRONT_LAYERS)}"
        )

    warnings: list[str] = []
    missing_optional = sorted(_OPTIONAL_LAYERS - set(front_layers))
    if missing_optional:
        warnings.append(f"optional front layer(s) not supplied: {missing_optional}")

    front_order = [name for name in _FRONT_LAYER_Z if name in front_layers]
    back_order = [name for name in _BACK_LAYER_Z if name in back_layers]

    def load_layers(
        layer_map: Mapping[str, str | Path], order: list[str], label: str
    ) -> tuple[dict[str, Image.Image], dict[str, tuple[int, int, int, int]]]:
        images: dict[str, Image.Image] = {}
        bboxes: dict[str, tuple[int, int, int, int]] = {}
        for name in order:
            image = load_image(safe_join(project.root, str(layer_map[name])))
            bbox = _opaque_bbox(np.array(image, dtype=np.uint8))
            if bbox is None:
                raise ForgeError(
                    f"import_layered: {label} layer {name!r} is fully transparent; "
                    "there is nothing to import"
                )
            images[name] = image
            bboxes[name] = bbox
        return images, bboxes

    front_images, front_bboxes = load_layers(front_layers, front_order, "front")
    back_images, back_bboxes = load_layers(back_layers, back_order, "back")

    ux0 = min(b[0] for b in front_bboxes.values())
    uy0 = min(b[1] for b in front_bboxes.values())
    ux1 = max(b[2] for b in front_bboxes.values())
    uy1 = max(b[3] for b in front_bboxes.values())
    if canvas is None:
        dx, dy = -ux0, -uy0
        canvas_size = (ux1 - ux0, uy1 - uy0)
    else:
        if canvas[0] < 1 or canvas[1] < 1:
            raise ForgeError(f"import_layered: canvas must be >= 1x1, got {canvas}")
        dx, dy = 0, 0
        canvas_size = canvas
        if ux0 < 0 or uy0 < 0 or ux1 > canvas[0] or uy1 > canvas[1]:
            raise ForgeError(
                f"import_layered: front layers span ({ux0}, {uy0})-({ux1}, {uy1}), which "
                f"exceeds the explicit canvas {canvas}; enlarge it or pass canvas=None "
                "to derive the canvas from the layer extents"
            )
    if max(canvas_size) > _LAYERED_CANVAS_WARN:
        warnings.append(
            f"canvas {canvas_size} is larger than {_LAYERED_CANVAS_WARN}px in at least "
            "one dimension; is this a single pixel-art sprite at its intended scale?"
        )
    for name, bbox in back_bboxes.items():
        if (
            bbox[0] + dx < 0
            or bbox[1] + dy < 0
            or bbox[2] + dx > canvas_size[0]
            or bbox[3] + dy > canvas_size[1]
        ):
            warnings.append(
                f"back layer {name!r} extends beyond the canvas and will clip when rendered"
            )

    front_composite = _composite_binary(
        [(np.array(front_images[n], dtype=np.uint8), dx, dy) for n in front_order],
        canvas_size,
    )
    composites = [front_composite]
    if back_order:
        composites.append(
            _composite_binary(
                [(np.array(back_images[n], dtype=np.uint8), dx, dy) for n in back_order],
                canvas_size,
            )
        )
    palette, unique_colors = _palette_from_composites(composites, max_colors, f"{asset_id}_palette")
    if unique_colors > max_colors:
        warnings.append(
            f"source art has {unique_colors} unique colours but max_colors={max_colors}; "
            "the palette keeps the most frequent ones and pixels of the rest are dropped"
        )
    resolved_palette = resolve_palette(palette)

    shifted_front = {
        name: (b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy) for name, b in front_bboxes.items()
    }
    anchors = _layered_anchors(shifted_front, canvas_size)

    regions: dict[str, Any] = {}

    def add_bitmap_region(region_name: str, image: Image.Image, anchor: str, layer_z: int) -> None:
        bitmap, report = png_to_bitmap(image, resolved_palette, snap=False, trim=True)
        if report.unmatched:
            dropped = sum(report.unmatched.values())
            warnings.append(
                f"layer {region_name!r}: {dropped} opaque pixel(s) matched no palette "
                "colour and were dropped (raise max_colors to keep them)"
            )
        # Fully-transparent layers were rejected at load time, so a trimmed box
        # always exists.
        assert report.trimmed_to is not None
        ax, ay = anchors[anchor]
        at = (report.trimmed_to[0] + dx - ax, report.trimmed_to[1] + dy - ay)
        shape = BitmapShape.model_validate({**bitmap, "at": list(at)})
        regions[region_name] = {
            "anchor": anchor,
            "layer": layer_z,
            "shapes": [shape.model_dump(mode="json")],
        }

    for name in front_order:
        add_bitmap_region(name, front_images[name], _REGION_ANCHOR[name], _FRONT_LAYER_Z[name])
    for name in back_order:
        add_bitmap_region(
            f"back_{name}", back_images[name], _REGION_ANCHOR[name], _BACK_LAYER_Z[name]
        )

    frame: dict[str, Any] = {"duration_ms": 200}
    if back_order:
        frame["transforms"] = {f"back_{name}": {"visible": False} for name in back_order}

    spec: dict[str, Any] = {
        "schema_version": 1,
        "asset": {
            "id": asset_id,
            "type": "character",
            "canvas": [canvas_size[0], canvas_size[1]],
            # ANI001 measures the baseline as the lowest opaque row of the
            # rendered frame, which is the bottom of the front union (e.g. a
            # ground shadow can sit below the feet anchor).
            "baseline_y": uy1 + dy - 1,
        },
        "palette": palette.model_dump(mode="json"),
        "directions": ["south"],
        "anchors": {name: [x, y] for name, (x, y) in anchors.items()},
        "regions": regions,
        "animations": {"idle": {"loop": True, "frames": [frame]}},
        # Imported pixels are authored elsewhere and must round-trip byte-exact;
        # the render-polish pass would recolor silhouette edges, so opt out.
        "export": {"polish": False},
        "validation": {"palette_limit": max(24, max_colors)},
    }
    parse_asset_doc(spec)  # fail here, before anything is written, on a bad spec

    spec_path = _rel(project.root, project.paths.asset_spec(asset_id))
    result = ImportLayeredResult(
        asset_id=asset_id,
        canvas=canvas_size,
        spec_path=spec_path,
        regions=front_order,
        back_regions=[f"back_{name}" for name in back_order],
        anchors=anchors,
        palette_id=palette.id,
        palette_size=len(palette.colors),
        revision=None,
        warnings=warnings,
        dry_run=dry_run,
    )
    if dry_run:
        return result

    project.save_asset(parse_asset_doc(spec))
    # Mirror import_region: the import lands as one `replace_spec` revision via
    # update_asset_spec, so it goes through the same revision machinery
    # (validation, the append-only log) as every other structural edit. The
    # asset is saved first because that path requires the doc to already exist.
    record = update_asset_spec(root, asset_id, spec, timestamp=timestamp)
    return result.model_copy(update={"revision": record})


def render_view(
    root: Path,
    asset_id: str,
    *,
    animation: str,
    direction: str,
    frame: int = 0,
    scale: int = 8,
    out_path: str | Path | None = None,
    art_direction: ArtDirection | None = None,
) -> ViewResult:
    """Render one frame with the vision-loop diagnostic overlays (declared baseline,
    every anchor, the frame's silhouette bbox, and an 8-source-pixel grid once
    `scale >= 4`) burned into a copy, upscaled, and written to a PNG under
    `build/<asset_id>/` by default.

    This exists so a vision-capable agent can look at the frame it just described in
    the spec and iterate, instead of authoring shape coordinates blind.
    """
    project = _project(root)
    doc = _require_sprite_doc(_load_doc(project, asset_id), "render_view")
    frames = render_asset_frames(
        doc, _sprite_backend(project, doc), art_direction=_art_direction(art_direction, doc)
    )
    key = (animation, direction, frame)
    canvas = frames.get(key)
    if canvas is None:
        raise ForgeError(
            f"no frame {animation!r}/{direction!r}#{frame} for asset {asset_id!r}; "
            f"available (animation, direction, frame): {sorted(frames)}"
        )

    upscaled = upscale_view(canvas, scale)
    baseline = doc.asset.baseline_y * scale if doc.asset.baseline_y is not None else None
    anchors = {name: (x * scale, y * scale) for name, (x, y) in doc.anchors.items()}
    grid = 8 * scale if scale >= 4 else 0
    annotated = annotate_frame(upscaled, baseline_y=baseline, anchors=anchors, bbox=True, grid=grid)

    rel_path = (
        str(out_path)
        if out_path is not None
        else f"build/{asset_id}/view_{animation}_{direction}_{frame}.png"
    )
    out = safe_join(project.root, rel_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    annotated.save_png(out)
    return ViewResult(
        asset_id=asset_id,
        path=_rel(project.root, out),
        width=annotated.width,
        height=annotated.height,
        scale=scale,
    )


def render_annotated_contact(
    root: Path,
    asset_id: str,
    *,
    scale: int = 4,
    out_path: str | Path | None = None,
    art_direction: ArtDirection | None = None,
) -> ViewResult:
    """Build the asset's sprite sheet and draw the vision-loop diagnostic overlays
    (baseline, anchors, per-cell silhouette bbox) onto a scaled copy, written to a PNG
    under `build/<asset_id>/` by default.

    This exists so a vision-capable agent can look at every frame of an asset at once
    and iterate, instead of authoring shape coordinates blind.
    """
    project = _project(root)
    doc = _require_sprite_doc(_load_doc(project, asset_id), "render_annotated_contact")
    sheet = _sprite_sheet(project, doc, art_direction=art_direction)
    contact = build_annotated_contact(
        sheet, baseline_y=doc.asset.baseline_y, anchors=doc.anchors, scale=scale
    )
    rel_path = (
        str(out_path) if out_path is not None else f"build/{asset_id}/{asset_id}_annotated.png"
    )
    out = safe_join(project.root, rel_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    contact.save_png(out)
    return ViewResult(
        asset_id=asset_id,
        path=_rel(project.root, out),
        width=contact.width,
        height=contact.height,
        scale=scale,
    )


def render_contact_sheet(
    root: Path,
    asset_id: str,
    *,
    scale: int = 1,
    out_path: str | Path | None = None,
    art_direction: ArtDirection | None = None,
) -> ViewResult:
    """Plain (non-diagnostic) contact sheet at an arbitrary scale, for `pixel-forge
    contact` without `--annotate`. Unlike `render_asset`'s cached contact_sheet output
    (always scale=1), this always re-renders fresh at the requested scale. Not
    exposed over MCP — `render_annotated_contact` is the vision-loop entry point;
    this is a plain-viewing convenience for the CLI only.
    """
    project = _project(root)
    doc = _require_sprite_doc(_load_doc(project, asset_id), "render_contact_sheet")
    sheet = _sprite_sheet(project, doc, art_direction=art_direction)
    contact = build_contact_sheet(sheet, scale=scale)
    rel_path = (
        str(out_path) if out_path is not None else f"build/{asset_id}/{asset_id}_contact_view.png"
    )
    out = safe_join(project.root, rel_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    contact.save_png(out)
    return ViewResult(
        asset_id=asset_id,
        path=_rel(project.root, out),
        width=contact.width,
        height=contact.height,
        scale=scale,
    )


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


def test_seams(
    root: Path, asset_id: str, *, art_direction: ArtDirection | None = None
) -> SeamReport:
    project = _project(root)
    doc = _load_doc(project, asset_id)
    if not isinstance(doc, TerrainAsset):
        raise ForgeError(
            f"asset {asset_id!r} is not a terrain asset; seam testing applies to terrain tiles only"
        )
    tiles = render_terrain_tiles(doc, art_direction=_art_direction(art_direction, doc))
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
    root: Path,
    asset_id: str,
    *,
    force: bool = False,
    timestamp: str | None = None,
    art_direction: ArtDirection | None = None,
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
        and _cached_build_is_trustworthy(project, doc)
    ):
        return cached

    _render(project, doc, force=True, dry_run=False, art_direction=art_direction)
    rendered = _existing_manifest(build_dir)
    if rendered is None:
        raise ForgeError(f"internal error: render_asset did not write a manifest for {asset_id!r}")

    preview_paths: dict[str, str] = {}
    if not isinstance(doc, TerrainAsset):
        preview_paths = generate_preview(
            project.root, asset_id, art_direction=art_direction
        ).preview_paths

    export_godot(project.root, asset_id, art_direction=art_direction)
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


def build_all(
    root: Path, *, force: bool = False, art_direction: ArtDirection | None = None
) -> BuildReport:
    project = _project(root)
    manifests: list[AssetManifest] = []
    failed: list[str] = []
    for asset_id in project.discover_assets():
        try:
            manifest = build_asset(root, asset_id, force=force, art_direction=art_direction)
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
