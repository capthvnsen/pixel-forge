"""Animation resolution: spec -> per-frame region transforms, deterministically."""

from __future__ import annotations

from pixel_forge.animation.cycles import (
    generate_procedural_frames,
    generate_walk_cycle,
    resolve_animation_frames,
)
from pixel_forge.animation.resolver import (
    ResolvedFrame,
    ResolvedTileFrame,
    animation_duration_ms,
    frames_for,
    merge_transforms,
    resolve_frames,
    resolve_sampled_frame,
    resolve_terrain_frames,
)
from pixel_forge.animation.timeline import (
    EASING_CURVES,
    EasingFn,
    Keyframe,
    bake_timeline,
    ease_bounce,
    ease_in,
    ease_in_out,
    ease_linear,
    ease_out,
    easing_curve,
    lerp_transform,
    resample_frames,
    sample_frames,
    sample_timeline,
)

__all__ = [
    "EASING_CURVES",
    "EasingFn",
    "Keyframe",
    "ResolvedFrame",
    "ResolvedTileFrame",
    "animation_duration_ms",
    "bake_timeline",
    "ease_bounce",
    "ease_in",
    "ease_in_out",
    "ease_linear",
    "ease_out",
    "easing_curve",
    "frames_for",
    "generate_procedural_frames",
    "generate_walk_cycle",
    "lerp_transform",
    "merge_transforms",
    "resample_frames",
    "resolve_animation_frames",
    "resolve_frames",
    "resolve_sampled_frame",
    "resolve_terrain_frames",
    "sample_frames",
    "sample_timeline",
]
