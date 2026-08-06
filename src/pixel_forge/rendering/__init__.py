"""Rendering: canvas primitives, the shape DSL, the local render backend, sheets/atlases."""

from __future__ import annotations

from pixel_forge.rendering.backend import RenderBackend, TileRenderBackend
from pixel_forge.rendering.canvas import Canvas
from pixel_forge.rendering.external import ExternalFrameBackend, compute_source_pins, verify_pins
from pixel_forge.rendering.local import (
    LocalRenderBackend,
    render_asset_frames,
    render_terrain_tiles,
)
from pixel_forge.rendering.shapes import draw_shape
from pixel_forge.rendering.sheet import (
    SeamResult,
    SheetCell,
    SpriteSheet,
    build_atlas,
    build_contact_sheet,
    build_seam_map,
    build_sprite_sheet,
    check_seams,
)

__all__ = [
    "Canvas",
    "ExternalFrameBackend",
    "LocalRenderBackend",
    "RenderBackend",
    "SeamResult",
    "SheetCell",
    "SpriteSheet",
    "TileRenderBackend",
    "build_atlas",
    "build_contact_sheet",
    "build_seam_map",
    "build_sprite_sheet",
    "check_seams",
    "compute_source_pins",
    "draw_shape",
    "render_asset_frames",
    "render_terrain_tiles",
    "verify_pins",
]
