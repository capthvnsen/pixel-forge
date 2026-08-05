"""Neutral `AnimationPlayer` track payload for prop-style layered transform animation.

Integration note: `schemas.manifest.AnimationPlayerTrack` (frozen, `extra="forbid"`)
has `node_path` + `property` + `keyframes` with `time_ms: int` — there is no
per-animation grouping field, and no `time_s` float. This module folds the
animation (and, when an asset has more than one direction, the direction) into
`node_path` as `f"{animation}/{region}"` or `f"{animation}/{direction}/{region}"`,
so the plugin must split on `/` to recover which `Animation` resource a track
belongs to. `time_ms` is the exact cumulative integer sum of `duration_ms`
(no float seconds conversion — the schema already stores ms elsewhere, so this
stays lossless and matches the rest of the codebase's integer-duration
convention). See the exporter task's integration-risk report for details.
"""

from __future__ import annotations

from collections.abc import Sequence

from pixel_forge.animation.resolver import ResolvedFrame
from pixel_forge.schemas.asset import SpriteAssetBase
from pixel_forge.schemas.manifest import AnimationKeyframe, AnimationPlayerTrack


def build_animation_player(
    doc: SpriteAssetBase, frames: Sequence[ResolvedFrame]
) -> list[AnimationPlayerTrack]:
    """One track per `(animation, region)` whose transform actually changes across
    frames — a region whose offset (or effective visibility) is constant for the
    whole animation is skipped, since a track of constant values is noise.
    `"position"` tracks carry the region's cumulative `offset`; `"visible"`
    tracks carry the effective visibility (`transform.visible`, defaulting to
    `True` when unset).
    """
    grouped: dict[tuple[str, str], list[ResolvedFrame]] = {}
    directions_per_animation: dict[str, set[str]] = {}
    for frame in frames:
        grouped.setdefault((frame.animation, frame.direction), []).append(frame)
        directions_per_animation.setdefault(frame.animation, set()).add(frame.direction)

    tracks: list[AnimationPlayerTrack] = []
    for (animation, direction), group in grouped.items():
        ordered = sorted(group, key=lambda f: f.index)
        times: list[int] = []
        cumulative = 0
        for frame in ordered:
            times.append(cumulative)
            cumulative += frame.duration_ms

        multi_direction = len(directions_per_animation[animation]) > 1
        prefix = f"{animation}/{direction}" if multi_direction else animation

        for region in doc.regions:
            offsets = [f.transforms[region].offset for f in ordered]
            if any(offset != offsets[0] for offset in offsets):
                tracks.append(
                    AnimationPlayerTrack(
                        node_path=f"{prefix}/{region}",
                        property="position",
                        keyframes=[
                            AnimationKeyframe(time_ms=t, value=offset)
                            for t, offset in zip(times, offsets, strict=True)
                        ],
                    )
                )

            visibles = [
                f.transforms[region].visible if f.transforms[region].visible is not None else True
                for f in ordered
            ]
            if any(visible != visibles[0] for visible in visibles):
                tracks.append(
                    AnimationPlayerTrack(
                        node_path=f"{prefix}/{region}",
                        property="visible",
                        keyframes=[
                            AnimationKeyframe(time_ms=t, value=visible)
                            for t, visible in zip(times, visibles, strict=True)
                        ],
                    )
                )

    return tracks
