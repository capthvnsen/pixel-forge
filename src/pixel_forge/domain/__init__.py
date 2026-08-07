"""Pure domain core: paths, project lifecycle, palette, geometry, hashing, YAML I/O."""

from __future__ import annotations

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
    ResolvedPalette,
    check_palette_limit,
    hex_to_rgba,
    palette_for_polish,
    resolve_palette,
    rgba_to_hex,
)
from pixel_forge.domain.paths import ProjectPaths, safe_join, validate_asset_id
from pixel_forge.domain.project import Project

__all__ = [
    "Project",
    "ProjectPaths",
    "Rect",
    "ResolvedPalette",
    "anchor_world_pos",
    "append_jsonl",
    "bbox_of_points",
    "check_palette_limit",
    "content_hash",
    "dump_asset_doc",
    "dump_yaml",
    "file_hash",
    "hex_to_rgba",
    "load_asset_doc",
    "load_jsonl",
    "load_yaml",
    "mirror_anchors",
    "mirror_point_x",
    "palette_for_polish",
    "resolve_palette",
    "rgba_to_hex",
    "safe_join",
    "short",
    "silhouette_area",
    "silhouette_centroid",
    "validate_asset_id",
]
