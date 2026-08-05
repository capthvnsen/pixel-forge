"""Assemble the neutral Godot import manifest and write it to disk.

Emits `<out_dir>/<asset_id>.forge.json`. This is JSON only — never `.tres`/`.tscn` —
a GDScript editor plugin (built separately) reads it and constructs native Godot
4.4 resources through Godot's own APIs.

Integration note: `GodotManifest` still has no field for `fps`/`duration_frames`
(see `spriteframes.derive_fps`) or float `time_s` keyframes (see
`animation.build_animation_player`) — everything else the exporter needs is
now present in `schemas.manifest`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from pixel_forge.animation.resolver import ResolvedFrame
from pixel_forge.domain.geometry import mirror_anchors
from pixel_forge.errors import ExportError
from pixel_forge.exporters.godot.animation import build_animation_player
from pixel_forge.exporters.godot.spriteframes import build_sprite_frames
from pixel_forge.exporters.godot.tileset import build_tileset
from pixel_forge.rendering.sheet import SheetCell, SpriteSheet
from pixel_forge.schemas.animation import ProceduralAnimationSpec
from pixel_forge.schemas.asset import AssetDocUnion, SpriteAssetBase, TerrainAsset
from pixel_forge.schemas.common import Vec2
from pixel_forge.schemas.manifest import AnimationPlayerExport, GodotImportSettings, GodotManifest

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:/")


def _normalize_texture_path(path: str) -> str:
    """Relative, forward-slash, project-root-relative. Never absolute, never `..`
    past the root. Backslashes (Windows-style input) are normalised to `/`.
    """
    normalized = path.replace("\\", "/")
    if normalized.startswith("/") or _WINDOWS_DRIVE_RE.match(normalized):
        raise ExportError(f"texture path must be relative to the project root: {path!r}")

    parts: list[str] = []
    for part in normalized.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                raise ExportError(f"texture path escapes the project root: {path!r}")
            parts.pop()
        else:
            parts.append(part)

    if not parts:
        raise ExportError(f"texture path is empty after normalization: {path!r}")
    return "/".join(parts)


def _pivots_for(doc: SpriteAssetBase) -> dict[str, Vec2]:
    """Per direction: the resolved `feet` anchor, falling back to bottom-centre of
    the canvas. Mirrored directions mirror every anchor first (matching the
    renderer's own mirroring convention in `domain.geometry`).
    """
    canvas_w, canvas_h = doc.asset.canvas
    fallback: Vec2 = (canvas_w // 2, canvas_h - 1)
    pivots: dict[str, Vec2] = {}
    for direction in doc.directions:
        mirror_src = doc.mirror.get(direction)
        anchors = mirror_anchors(doc.anchors, canvas_w) if mirror_src else doc.anchors
        pivots[direction] = anchors.get("feet", fallback)
    return pivots


def _events_for(doc: SpriteAssetBase) -> dict[str, list[list[str]]]:
    """animation -> per-frame event lists. Events live on `FrameSpec`, which is
    shared by every direction of an animation, so there is no direction axis here.
    """
    return {
        name: [list(frame.events) for frame in anim.frames] for name, anim in doc.animations.items()
    }


def _procedural_for(doc: SpriteAssetBase) -> dict[str, ProceduralAnimationSpec]:
    return {
        name: anim.procedural
        for name, anim in doc.animations.items()
        if anim.procedural is not None
    }


def _pick_terrain_texture(textures: Mapping[str, str]) -> str:
    if "atlas" in textures:
        return textures["atlas"]
    if len(textures) == 1:
        return next(iter(textures.values()))
    raise ExportError(
        "terrain doc with multiple textures requires an 'atlas' entry in texture_paths"
    )


def build_godot_manifest(
    doc: AssetDocUnion,
    *,
    sheet: SpriteSheet | None = None,
    texture_paths: Mapping[str, str],
    spec_hash: str,
    atlas_cells: Mapping[str, SheetCell] | None = None,
    frames: Sequence[ResolvedFrame] | None = None,
) -> GodotManifest:
    """A terrain doc must not emit `sprite_frames` (pass `sheet`/`frames`); a
    sprite doc must not emit `tileset` (pass `atlas_cells`) — either mismatch
    raises `ExportError` rather than silently emitting an empty section.
    """
    textures = {name: _normalize_texture_path(path) for name, path in texture_paths.items()}

    if isinstance(doc, TerrainAsset):
        if sheet is not None or frames is not None:
            raise ExportError("terrain doc must not emit sprite_frames")
        if atlas_cells is None:
            raise ExportError("terrain doc requires atlas_cells to build a tileset")
        tileset = build_tileset(doc, atlas_cells, _pick_terrain_texture(textures))
        return GodotManifest(
            asset_id=doc.asset.id,
            asset_type=doc.asset.type,
            spec_hash=spec_hash,
            textures=textures,
            baseline_y=doc.asset.baseline_y,
            tileset=tileset,
            import_settings=GodotImportSettings(),
        )

    if atlas_cells is not None:
        raise ExportError("sprite doc must not emit a tileset")
    if sheet is None or frames is None:
        raise ExportError("sprite doc requires sheet and frames to build sprite_frames")

    return GodotManifest(
        asset_id=doc.asset.id,
        asset_type=doc.asset.type,
        spec_hash=spec_hash,
        textures=textures,
        sprite_frames=build_sprite_frames(doc, sheet, frames),
        pivots=_pivots_for(doc),
        baseline_y=doc.asset.baseline_y,
        events=_events_for(doc),
        animation_player=AnimationPlayerExport(tracks=build_animation_player(doc, frames)),
        procedural=_procedural_for(doc),
        import_settings=GodotImportSettings(),
    )


def write_godot_manifest(manifest: GodotManifest, out_dir: Path) -> Path:
    """Writes `<out_dir>/<asset_id>.forge.json`, `sort_keys=True` + trailing
    newline, byte-identical across runs for identical input. Creates `out_dir`.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{manifest.asset_id}.forge.json"
    payload = manifest.model_dump(mode="json")
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
