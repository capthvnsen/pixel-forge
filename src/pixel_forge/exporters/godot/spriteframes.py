"""Neutral `SpriteFrames` payload: one entry per (animation, direction) pair.

Integration note: `schemas.manifest.SpriteFrameEntry` only carries `rect` and
`duration_ms` (both `extra="forbid"`, owned by another agent, never edited here).
There is no field for `fps`, `duration_frames`, `speed_scale`, or per-frame
`events` — Godot's `SpriteFrames` resource needs an fps + a per-frame duration
*multiple* of `1/fps`, but the manifest schema doesn't carry that computation's
output anywhere. `derive_fps`/`duration_frames_for` below are kept as the
authoritative, tested reference implementation of the formula the GDScript
plugin must replicate bit-for-bit against the raw `duration_ms` values this
module does emit; they are not wired into `build_sprite_frames` because there
is nowhere in the schema to put their output. See the exporter task's
integration-risk report for the exact recommendation.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from functools import reduce

from pixel_forge.animation.resolver import ResolvedFrame
from pixel_forge.errors import ExportError
from pixel_forge.rendering.sheet import SpriteSheet
from pixel_forge.schemas.asset import SpriteAssetBase
from pixel_forge.schemas.manifest import AtlasRect, SpriteFrameEntry, SpriteFramesAnimation

_DEFAULT_MAX_FPS = 60.0


def derive_fps(durations: Sequence[int], *, max_fps: float = _DEFAULT_MAX_FPS) -> float:
    """`fps = min(1000 / gcd(durations_ms), max_fps)`.

    Equal durations collapse to `fps = 1000 / duration` (gcd of one repeated value
    is the value itself), which in turn makes every `duration_frames` exactly
    `1.0` — see `duration_frames_for`. A duration set whose gcd is 1ms would
    otherwise imply an absurd 1000fps animation, so the result is capped at
    `max_fps` (default 60, a sane ceiling for hand-authored pixel-art timing).
    """
    if not durations:
        raise ExportError("derive_fps: durations must be non-empty")
    if any(d <= 0 for d in durations):
        raise ExportError(f"derive_fps: durations must all be > 0, got {list(durations)!r}")
    gcd_ms = reduce(math.gcd, durations)
    return min(1000.0 / gcd_ms, max_fps)


def duration_frames_for(durations: Sequence[int], fps: float) -> list[float]:
    """Per-frame Godot `duration` multiplier: `duration_ms * fps / 1000`."""
    return [d * fps / 1000.0 for d in durations]


def build_sprite_frames(
    doc: SpriteAssetBase,
    sheet: SpriteSheet,
    frames: Sequence[ResolvedFrame],
) -> dict[str, SpriteFramesAnimation]:
    """One `SpriteFramesAnimation` per `(animation, direction)`, named
    `f"{animation}_{direction}"` — Godot `SpriteFrames` animation names are flat,
    so direction has to be folded into the name. `loop` comes from
    `doc.animations[animation].loop`; frames are ordered by `ResolvedFrame.index`
    and carry the packed atlas rect plus the raw `duration_ms`.
    """
    grouped: dict[tuple[str, str], list[ResolvedFrame]] = {}
    for frame in frames:
        grouped.setdefault((frame.animation, frame.direction), []).append(frame)

    result: dict[str, SpriteFramesAnimation] = {}
    for (animation, direction), group in grouped.items():
        loop = doc.animations[animation].loop
        entries = []
        for frame in sorted(group, key=lambda f: f.index):
            cell = sheet.cell_for(animation, direction, frame.index)
            entries.append(
                SpriteFrameEntry(
                    rect=AtlasRect(x=cell.x, y=cell.y, w=cell.w, h=cell.h),
                    duration_ms=frame.duration_ms,
                )
            )
        result[f"{animation}_{direction}"] = SpriteFramesAnimation(loop=loop, frames=entries)
    return result
