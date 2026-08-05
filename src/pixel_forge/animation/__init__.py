"""Animation resolution: spec -> per-frame region transforms, deterministically."""

from __future__ import annotations

from pixel_forge.animation.resolver import (
    ResolvedFrame,
    ResolvedTileFrame,
    animation_duration_ms,
    frames_for,
    merge_transforms,
    resolve_frames,
    resolve_terrain_frames,
)
from pixel_forge.animation.timeline import (
    Keyframe,
    bake_timeline,
    lerp_transform,
    sample_timeline,
)

__all__ = [
    "Keyframe",
    "ResolvedFrame",
    "ResolvedTileFrame",
    "animation_duration_ms",
    "bake_timeline",
    "frames_for",
    "lerp_transform",
    "merge_transforms",
    "resolve_frames",
    "resolve_terrain_frames",
    "sample_timeline",
]
