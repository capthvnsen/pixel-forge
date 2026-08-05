"""Animation-integrity rules: cross-frame consistency checks (ANI001-ANI009).

Deterministic unless marked heuristic. Position/anchor/duration checks read
`RuleContext.resolved` (already-merged per-frame metadata); pixel-content
checks read the rendered canvases in `RuleContext.frames`.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from pixel_forge.animation.resolver import ResolvedFrame
from pixel_forge.domain.geometry import anchor_world_pos
from pixel_forge.domain.palette import rgba_to_hex
from pixel_forge.rendering.canvas import Canvas
from pixel_forge.schemas import Finding, Severity, SpriteAssetBase
from pixel_forge.validation.engine import RuleContext, make_finding, register

_SPRITE_TYPES = ("character", "enemy", "prop")


def _sprite_doc(ctx: RuleContext) -> SpriteAssetBase | None:
    return ctx.doc if isinstance(ctx.doc, SpriteAssetBase) else None


def _group_by_anim_direction(
    resolved: Sequence[ResolvedFrame],
) -> dict[tuple[str, str], list[ResolvedFrame]]:
    groups: dict[tuple[str, str], list[ResolvedFrame]] = {}
    for frame in resolved:
        groups.setdefault((frame.animation, frame.direction), []).append(frame)
    for key in groups:
        groups[key].sort(key=lambda f: f.index)
    return groups


def _canvases_or_none(
    ctx: RuleContext, animation: str, direction: str, frames: Sequence[ResolvedFrame]
) -> list[Canvas] | None:
    result: list[Canvas] = []
    for frame in frames:
        canvas = ctx.frames.get((animation, direction, frame.index))
        if canvas is None:
            return None
        result.append(canvas)
    return result


def _feet_like_anchor(doc: SpriteAssetBase) -> tuple[str, list[str]] | None:
    anchor_name: str | None
    if "feet" in doc.anchors:
        anchor_name = "feet"
    else:
        anchor_name = None
        for region_name, region in doc.regions.items():
            lname = region_name.lower()
            if "shadow" in lname or "body" in lname:
                anchor_name = region.anchor
                break
        if anchor_name is None:
            return None
    region_names = sorted(
        name for name, region in doc.regions.items() if region.anchor == anchor_name
    )
    if not region_names:
        return None
    return anchor_name, region_names


@register(
    "ANI001",
    severity="error",
    kind="deterministic",
    applies_to=_SPRITE_TYPES,
    description="Baseline drift: measured lowest opaque row must equal doc.asset.baseline_y.",
)
def _ani001(ctx: RuleContext) -> list[Finding]:
    if not ctx.doc.validation.require_stable_baseline:
        return []
    baseline_y = ctx.doc.asset.baseline_y
    if baseline_y is None:
        return []
    findings = []
    for key in sorted(ctx.frames):
        animation, direction, index = key
        bbox = ctx.frames[key].bbox()
        if bbox is None:
            continue
        measured = bbox[3] - 1
        if measured != baseline_y:
            findings.append(
                make_finding(
                    ctx,
                    "ANI001",
                    "error",
                    "deterministic",
                    animation=animation,
                    direction=direction,
                    frame=index,
                    message=(
                        f"measured baseline y={measured} differs from declared "
                        f"baseline_y={baseline_y}"
                    ),
                    remediation="adjust region offsets so the lowest opaque row matches baseline_y",
                    measurements={
                        "measured_baseline_y": measured,
                        "expected_baseline_y": baseline_y,
                        "drift_px": abs(measured - baseline_y),
                    },
                )
            )
    return findings


@register(
    "ANI002",
    severity="error",
    kind="deterministic",
    applies_to=_SPRITE_TYPES,
    description=(
        "Foot-anchor drift: the 'feet' anchor (or the anchor of a region named "
        "*shadow*/*body*, else skipped) must resolve to one position across all "
        "frames of an animation+direction, when require_stable_anchors."
    ),
)
def _ani002(ctx: RuleContext) -> list[Finding]:
    if not ctx.doc.validation.require_stable_anchors:
        return []
    doc = _sprite_doc(ctx)
    if doc is None:
        return []
    found = _feet_like_anchor(doc)
    if found is None:
        return []
    anchor_name, region_names = found

    findings = []
    for (animation, direction), frames in sorted(_group_by_anim_direction(ctx.resolved).items()):
        for region_name in region_names:
            positions = {
                anchor_world_pos(doc.anchors, anchor_name, frame.transforms[region_name].offset)
                for frame in frames
            }
            if len(positions) > 1:
                findings.append(
                    make_finding(
                        ctx,
                        "ANI002",
                        "error",
                        "deterministic",
                        animation=animation,
                        direction=direction,
                        region=region_name,
                        message=(
                            f"anchor {anchor_name!r} (region {region_name!r}) resolves to "
                            f"{len(positions)} distinct positions across frames: "
                            f"{sorted(positions)}"
                        ),
                        remediation="keep the foot/body anchor offset constant across an animation",
                        measurements={"anchor": anchor_name, "distinct_positions": len(positions)},
                    )
                )
    return findings


@register(
    "ANI003",
    severity="error",
    kind="deterministic",
    applies_to=_SPRITE_TYPES,
    description=(
        "Pivot drift: frame bbox centre-x must not move by more than 2px between "
        "consecutive frames of a looping animation (error); non-looping emits warning."
    ),
)
def _ani003(ctx: RuleContext) -> list[Finding]:
    doc = _sprite_doc(ctx)
    if doc is None:
        return []
    findings = []
    for (animation, direction), frames in sorted(_group_by_anim_direction(ctx.resolved).items()):
        anim_spec = doc.animations.get(animation)
        looping = anim_spec.loop if anim_spec is not None else True
        severity: Severity = "error" if looping else "warning"
        prev_cx: float | None = None
        prev_index: int | None = None
        for frame in frames:
            canvas = ctx.frames.get((animation, direction, frame.index))
            if canvas is None:
                continue
            bbox = canvas.bbox()
            if bbox is None:
                continue
            x0, _, x1, _ = bbox
            cx = (x0 + x1 - 1) / 2
            if prev_cx is not None and prev_index is not None:
                delta = abs(cx - prev_cx)
                if delta > 2:
                    findings.append(
                        make_finding(
                            ctx,
                            "ANI003",
                            severity,
                            "deterministic",
                            animation=animation,
                            direction=direction,
                            frame=frame.index,
                            message=(
                                f"frame bbox centre-x moved {delta:.1f}px from frame "
                                f"{prev_index} to {frame.index}"
                            ),
                            remediation="keep the silhouette centred; large horizontal jumps pop",
                            measurements={
                                "delta_px": delta,
                                "previous_frame": prev_index,
                                "current_frame": frame.index,
                            },
                        )
                    )
            prev_cx, prev_index = cx, frame.index
    return findings


@register(
    "ANI004",
    severity="error",
    kind="deterministic",
    applies_to=_SPRITE_TYPES,
    description=(
        "Attachment-anchor drift: an anchor other than 'feet' must not move between "
        "frames unless that frame's own transforms dict explicitly set an offset for "
        "the region using it."
    ),
)
def _ani004(ctx: RuleContext) -> list[Finding]:
    doc = _sprite_doc(ctx)
    if doc is None:
        return []
    findings = []
    for anchor_name in sorted(doc.anchors):
        if anchor_name == "feet":
            continue
        region_names = sorted(
            name for name, region in doc.regions.items() if region.anchor == anchor_name
        )
        if not region_names:
            continue
        for (animation, direction), frames in sorted(
            _group_by_anim_direction(ctx.resolved).items()
        ):
            anim_spec = doc.animations.get(animation)
            for region_name in region_names:
                prev_pos: tuple[int, int] | None = None
                for frame in frames:
                    pos = anchor_world_pos(
                        doc.anchors, anchor_name, frame.transforms[region_name].offset
                    )
                    explicit = anim_spec is not None and region_name in (
                        anim_spec.frames[frame.index].transforms
                    )
                    if prev_pos is not None and pos != prev_pos and not explicit:
                        findings.append(
                            make_finding(
                                ctx,
                                "ANI004",
                                "error",
                                "deterministic",
                                animation=animation,
                                direction=direction,
                                frame=frame.index,
                                region=region_name,
                                message=(
                                    f"anchor {anchor_name!r} moved from {prev_pos} to {pos} "
                                    "without an explicit frame transform"
                                ),
                                remediation=(
                                    f"add an explicit transforms entry for region "
                                    f"{region_name!r} in this frame, or remove the drift"
                                ),
                                measurements={
                                    "anchor": anchor_name,
                                    "previous_x": prev_pos[0],
                                    "previous_y": prev_pos[1],
                                    "current_x": pos[0],
                                    "current_y": pos[1],
                                },
                            )
                        )
                    prev_pos = pos
    return findings


@register(
    "ANI005",
    severity="warning",
    kind="heuristic",
    applies_to=_SPRITE_TYPES,
    description=(
        "Loop popping: for a looping animation, the last frame and the first frame "
        "differ by more than 35% of opaque pixels (XOR of opaque masks over the "
        "larger of the two opaque counts)."
    ),
)
def _ani005(ctx: RuleContext) -> list[Finding]:
    doc = _sprite_doc(ctx)
    if doc is None:
        return []
    findings = []
    for (animation, direction), frames in sorted(_group_by_anim_direction(ctx.resolved).items()):
        anim_spec = doc.animations.get(animation)
        if anim_spec is None or not anim_spec.loop or len(frames) < 2:
            continue
        first = ctx.frames.get((animation, direction, frames[0].index))
        last = ctx.frames.get((animation, direction, frames[-1].index))
        if first is None or last is None:
            continue
        first_mask = first.array[..., 3] != 0
        last_mask = last.array[..., 3] != 0
        diff = int(np.count_nonzero(first_mask != last_mask))
        denom = max(int(np.count_nonzero(first_mask)), int(np.count_nonzero(last_mask)), 1)
        ratio = diff / denom
        if ratio > 0.35:
            findings.append(
                make_finding(
                    ctx,
                    "ANI005",
                    "warning",
                    "heuristic",
                    animation=animation,
                    direction=direction,
                    message=(
                        f"last frame differs from first frame by {ratio:.1%} of opaque "
                        "pixels; likely to visibly pop on loop"
                    ),
                    remediation="make the last frame closer to the first, or add a blend frame",
                    measurements={"differing_pixels": diff, "ratio": ratio},
                )
            )
    return findings


@register(
    "ANI006",
    severity="warning",
    kind="heuristic",
    applies_to=_SPRITE_TYPES,
    description="Palette flicker: a colour appears in frame N and N+2 but not in frame N+1.",
)
def _ani006(ctx: RuleContext) -> list[Finding]:
    findings = []
    for (animation, direction), frames in sorted(_group_by_anim_direction(ctx.resolved).items()):
        if len(frames) < 3:
            continue
        canvases = _canvases_or_none(ctx, animation, direction, frames)
        if canvases is None:
            continue
        for i in range(len(canvases) - 2):
            colors_n = canvases[i].colors()
            colors_n1 = canvases[i + 1].colors()
            colors_n2 = canvases[i + 2].colors()
            flicker = (colors_n & colors_n2) - colors_n1
            if flicker:
                findings.append(
                    make_finding(
                        ctx,
                        "ANI006",
                        "warning",
                        "heuristic",
                        animation=animation,
                        direction=direction,
                        frame=frames[i + 1].index,
                        message=(
                            f"{len(flicker)} colour(s) present in frames {frames[i].index} and "
                            f"{frames[i + 2].index} but absent from frame {frames[i + 1].index}"
                        ),
                        remediation="check for a missing colour swap or dropped shape mid-cycle",
                        measurements={
                            "flicker_color_count": len(flicker),
                            "colors_hex": ",".join(sorted(rgba_to_hex(c) for c in flicker)),
                        },
                    )
                )
    return findings


@register(
    "ANI007",
    severity="error",
    kind="deterministic",
    applies_to=_SPRITE_TYPES,
    description=(
        "A declared animation must have at least one frame, and every resolved frame "
        "must have a corresponding rendered canvas in RuleContext.frames."
    ),
)
def _ani007(ctx: RuleContext) -> list[Finding]:
    doc = _sprite_doc(ctx)
    findings = []
    if doc is not None:
        for animation, spec in doc.animations.items():
            if len(spec.frames) == 0:
                findings.append(
                    make_finding(
                        ctx,
                        "ANI007",
                        "error",
                        "deterministic",
                        animation=animation,
                        message=f"animation {animation!r} declares zero frames",
                        remediation="add at least one frame to the animation",
                        measurements={"frame_count": 0},
                    )
                )
    for frame in ctx.resolved:
        key = (frame.animation, frame.direction, frame.index)
        if key not in ctx.frames:
            findings.append(
                make_finding(
                    ctx,
                    "ANI007",
                    "error",
                    "deterministic",
                    animation=frame.animation,
                    direction=frame.direction,
                    frame=frame.index,
                    message="rendered frame is missing from the render context",
                    remediation="ensure the renderer produced a canvas for every resolved frame",
                    measurements={"missing": 1},
                )
            )
    return findings


@register(
    "ANI008",
    severity="warning",
    kind="heuristic",
    applies_to=_SPRITE_TYPES,
    description=(
        "Unexpected silhouette-volume change: opaque-pixel count between consecutive "
        "frames changes by more than 40% relative to the previous frame's count."
    ),
)
def _ani008(ctx: RuleContext) -> list[Finding]:
    findings = []
    for (animation, direction), frames in sorted(_group_by_anim_direction(ctx.resolved).items()):
        prev_count: int | None = None
        for frame in frames:
            canvas = ctx.frames.get((animation, direction, frame.index))
            if canvas is None:
                continue
            count = canvas.opaque_count()
            if prev_count is not None:
                ratio = abs(count - prev_count) / max(prev_count, 1)
                if ratio > 0.4:
                    findings.append(
                        make_finding(
                            ctx,
                            "ANI008",
                            "warning",
                            "heuristic",
                            animation=animation,
                            direction=direction,
                            frame=frame.index,
                            message=(
                                f"opaque pixel count changed {ratio:.1%} to frame "
                                f"{frame.index} ({prev_count}px -> {count}px)"
                            ),
                            remediation="check for a dropped/duplicated region between frames",
                            measurements={
                                "previous_count": prev_count,
                                "current_count": count,
                                "ratio": ratio,
                            },
                        )
                    )
            prev_count = count
    return findings


@register(
    "ANI009",
    severity="error",
    kind="deterministic",
    applies_to=_SPRITE_TYPES,
    description=(
        "Directional inconsistency: every direction of the same animation must have "
        "the same frame count and the same per-frame durations."
    ),
)
def _ani009(ctx: RuleContext) -> list[Finding]:
    by_animation: dict[str, dict[str, list[ResolvedFrame]]] = {}
    for frame in ctx.resolved:
        by_animation.setdefault(frame.animation, {}).setdefault(frame.direction, []).append(frame)

    findings = []
    for animation in sorted(by_animation):
        per_direction = by_animation[animation]
        directions = sorted(per_direction)
        if len(directions) < 2:
            continue
        for direction in directions:
            per_direction[direction].sort(key=lambda f: f.index)
        durations = {
            direction: tuple(f.duration_ms for f in per_direction[direction])
            for direction in directions
        }
        if len(set(durations.values())) > 1:
            counts = {direction: len(durations[direction]) for direction in directions}
            findings.append(
                make_finding(
                    ctx,
                    "ANI009",
                    "error",
                    "deterministic",
                    animation=animation,
                    message=(
                        f"directions {directions} of animation {animation!r} disagree on "
                        f"frame count/durations: {durations}"
                    ),
                    remediation="use the same frame count and durations in every direction",
                    measurements={
                        "direction_count": len(directions),
                        **{f"frames_{d}": counts[d] for d in directions},
                    },
                )
            )
    return findings
