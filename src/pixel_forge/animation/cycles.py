"""Deterministic procedural animation generators (walk cycles).

A spec can declare ``procedural: {shader: walk_cycle, params: {...}}`` on an
animation instead of hand-authoring every frame's offsets. `generate_frames`
turns that declaration into a list of ordinary `FrameSpec`\\ s, using the doc's
own regions (discovered by name convention: names containing ``leg``/``arm``/
``body``/``head``, split into left/right by an ``l``/``left``/``r``/``right``
token) and the existing anchor/transform model — nothing about the renderer
changes, and every downstream consumer (validators, exporters, preview) sees
plain frames.

The generator is a pure function of ``(doc, params)``: no randomness, no clock,
identical output on every call, so repeated renders stay byte-identical. The
walk cycle itself is a classic 8-phase loop — leg scissor with an anti-phased
swing-phase foot lift + arm counter-swing (with a small pendulum arc) +
sinusoidal vertical body bob (two contacts per cycle) and optional smooth,
volume-preserving contact squash — at the 90-150 ms per-frame cadence of the
quality bar.

The whole upper body moves as one mass: the discovered ``head`` region rides the
same bob as the ``body`` region (deliberately *without* the squash's top-edge
shift — stacking it would make the passing-frame head top step 2px in one
frame), so the head cannot telescope off the torso while the torso pumps. The
swing leg lifts during its forward phase (one foot planted, one raised), so the
cycle reads as an alternating walk rather than a shuffle, and the contact squash
ramps over adjacent frames (anticipate -> contact -> recover) with a horizontal
counter-scale so volume is roughly preserved and no single frame snaps.

Regions anchored at the feet anchor (or named ``shadow``) are never offset, so
the baseline and foot-anchor stability rules keep holding; generated frames
always carry an explicit transform for every region they move, which is what the
attachment-anchor drift rule requires.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pixel_forge.animation.timeline import _round_half_away_from_zero
from pixel_forge.errors import ForgeError
from pixel_forge.schemas.animation import AnimationSpec, FrameSpec, ProceduralAnimationSpec
from pixel_forge.schemas.common import RegionTransform

if TYPE_CHECKING:
    from pixel_forge.schemas.asset import SpriteAssetBase

_WALK_DEFAULT_FRAMES = 8
_WALK_DEFAULT_DURATION_MS = 110


@dataclass(frozen=True)
class WalkRoles:
    """Regions a walk cycle can drive, discovered from the doc by name."""

    body: str | None
    head: str | None
    leg_left: str | None
    leg_right: str | None
    arm_left: str | None
    arm_right: str | None
    # Regions that must never be offset (shadow / anything anchored at 'feet'):
    # they keep the baseline and foot-anchor checks stable.
    static: frozenset[str]


def _side(name: str) -> str | None:
    for token in name.split("_"):
        if token in ("l", "left"):
            return "left"
        if token in ("r", "right"):
            return "right"
    return None


def _discover_roles(doc: SpriteAssetBase) -> WalkRoles:
    body: str | None = None
    head: str | None = None
    leg_left: str | None = None
    leg_right: str | None = None
    arm_left: str | None = None
    arm_right: str | None = None
    static: set[str] = set()
    feet_anchor = "feet" if "feet" in doc.anchors else None

    for name, region in doc.regions.items():
        lower = name.lower()
        if "shadow" in lower or (feet_anchor is not None and region.anchor == feet_anchor):
            static.add(name)
        elif body is None and "body" in lower:
            body = name
        elif head is None and "head" in lower:
            head = name

    for name in doc.regions:
        if name in static:
            continue
        lower = name.lower()
        side = _side(lower)
        if "leg" in lower:
            if side == "left" and leg_left is None:
                leg_left = name
            elif side == "right" and leg_right is None:
                leg_right = name
        elif "arm" in lower:
            if side == "left" and arm_left is None:
                arm_left = name
            elif side == "right" and arm_right is None:
                arm_right = name

    return WalkRoles(
        body=body,
        head=head,
        leg_left=leg_left,
        leg_right=leg_right,
        arm_left=arm_left,
        arm_right=arm_right,
        static=frozenset(static),
    )


def _param_int(
    params: Mapping[str, float | int | str | bool], name: str, default: int
) -> int:
    raw = params.get(name, default)
    if isinstance(raw, bool):
        raise ForgeError(f"walk_cycle param {name!r} must be an integer, got bool")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    if isinstance(raw, str) and raw.lstrip("-").isdigit():
        return int(raw)
    raise ForgeError(f"walk_cycle param {name!r} must be an integer, got {raw!r}")


def _param_bool(
    params: Mapping[str, float | int | str | bool], name: str, default: bool
) -> bool:
    raw = params.get(name, default)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        if raw in ("true", "True", "1"):
            return True
        if raw in ("false", "False", "0"):
            return False
    raise ForgeError(f"walk_cycle param {name!r} must be a bool, got {raw!r}")


def generate_walk_cycle(
    doc: SpriteAssetBase, params: Mapping[str, float | int | str | bool]
) -> list[FrameSpec]:
    """Generate a deterministic walk cycle for `doc`.

    Params (all optional):
    - ``frames`` (int, default 8): frames per cycle, >= 2.
    - ``duration_ms`` (int, default 110): per-frame duration, >= 1.
    - ``swing`` (int, default 1): horizontal leg swing amplitude in px.
    - ``arm_swing`` (int, default 2): arm counter-swing amplitude in px.
    - ``bob`` (int, default 1): vertical body bob amplitude in px.
    - ``lift`` (int, default 2): swing-phase foot lift amplitude in px (the leg
      that is swinging forward is raised this many px; the planted leg stays).
    - ``squash`` (bool, default false): smooth contact squash with a horizontal
      volume counter-scale instead of none.

    The upper body moves as one mass: the discovered ``head`` region rides the
    same bob as the ``body`` (without the squash's top-edge shift — stacking
    bob + squash-shift makes the passing-frame head top step 2px in one frame),
    so the head cannot telescope off the torso. The legs scissor with an
    anti-phased
    swing-phase lift — one foot planted, one raised — so the cycle reads as an
    alternating walk, not a shuffle. The contact squash ramps over adjacent
    frames (anticipate -> contact -> recover) at 1px granularity with a
    volume-preserving horizontal counter-scale, so no single frame snaps.

    The cycle is phase-periodic: the pose after the last frame equals the pose
    at frame 0, so the loop closes without a pose jump.
    """
    frame_count = _param_int(params, "frames", _WALK_DEFAULT_FRAMES)
    duration_ms = _param_int(params, "duration_ms", _WALK_DEFAULT_DURATION_MS)
    swing = _param_int(params, "swing", 1)
    arm_swing = _param_int(params, "arm_swing", 2)
    bob = _param_int(params, "bob", 1)
    lift = _param_int(params, "lift", 2)
    squash = _param_bool(params, "squash", False)

    if frame_count < 2:
        raise ForgeError(f"walk_cycle param 'frames' must be >= 2, got {frame_count}")
    if duration_ms < 1:
        raise ForgeError(f"walk_cycle param 'duration_ms' must be >= 1, got {duration_ms}")
    if swing < 0 or arm_swing < 0 or bob < 0 or lift < 0:
        raise ForgeError(
            "walk_cycle params 'swing'/'arm_swing'/'bob'/'lift' must be >= 0 "
            f"(got swing={swing}, arm_swing={arm_swing}, bob={bob}, lift={lift})"
        )

    roles = _discover_roles(doc)
    frames: list[FrameSpec] = []
    for i in range(frame_count):
        phase = 2.0 * math.pi * i / frame_count
        sin_p = math.sin(phase)
        cos_p = math.cos(phase)
        transforms: dict[str, RegionTransform] = {}

        # Body bob: 0 at contact (legs apart, body low), max at passing (legs
        # together, body high). A plain sinusoid — the natural walk curve, and
        # at 1px granularity it keeps the loop-wrap step small. Every limb rides
        # this same bob so the whole character pumps as one mass.
        bob_y = -_round_half_away_from_zero(bob * abs(sin_p))

        if roles.leg_left is not None and roles.leg_right is not None:
            # Scissor in x, plus an anti-phased swing-phase foot lift: the leg
            # that is moving forward (its swing) is raised; the leg moving back
            # is planted. The lift is sin^2-shaped so it is zero at both
            # contacts (footfall) and peaks mid-swing — no lift pops. The legs
            # also ride the body bob (the whole character pumps as one mass), so
            # the character's bottom row alternates frame to frame instead of
            # being pinned by an eternally planted foot.
            leg_x = _round_half_away_from_zero(swing * cos_p)
            swing_sq = lift * sin_p * sin_p
            lift_l = -_round_half_away_from_zero(swing_sq) if sin_p < 0 else 0
            lift_r = -_round_half_away_from_zero(swing_sq) if sin_p > 0 else 0
            transforms[roles.leg_left] = RegionTransform(offset=(leg_x, bob_y + lift_l))
            transforms[roles.leg_right] = RegionTransform(offset=(-leg_x, bob_y + lift_r))

        if roles.arm_left is not None and roles.arm_right is not None:
            # Counter-swing: arms oppose the legs (negative phase) at a larger
            # default amplitude so the swing reads, with a small pendulum arc
            # (arms rise slightly as they pass the body) so the arm moves in y
            # as well as x. Arms ride the bob too, keeping them attached to the
            # shoulders of the pumping torso.
            arm_x = _round_half_away_from_zero(arm_swing * -cos_p)
            arm_y = bob_y - _round_half_away_from_zero(arm_swing * (1.0 - abs(cos_p)))
            transforms[roles.arm_left] = RegionTransform(offset=(arm_x, arm_y))
            transforms[roles.arm_right] = RegionTransform(offset=(-arm_x, arm_y))

        # Body squash rides on top of the shared bob.
        scale_size = (0, 0)
        if roles.body is not None:
            if squash:
                # Squash at contact: |cos|-shaped dip (anticipate -> contact ->
                # recover over adjacent frames), -1 vertical with a +1 horizontal
                # counter-scale so volume is roughly preserved; 0 at passing.
                v = -_round_half_away_from_zero(abs(cos_p))
                scale_size = (-v, v)
            transforms[roles.body] = RegionTransform(
                offset=(0, bob_y), scale_size=scale_size
            )

        if roles.head is not None:
            # Drive the discovered head with the same bob as the body so the
            # head and torso pump as one mass — the rendered head top row must
            # not stay pixel-static while the torso pumps. The squash's top-edge
            # shift is deliberately NOT stacked on the bob here: at passing the
            # bob drops while the squash-shift relaxes, so adding the two makes
            # the head top step -2 in a single frame (a visible 2px pop).
            # Riding the bob alone keeps every head-top step at 1px; the cost
            # is a slightly larger head/torso gap at contact, which reads as
            # the squash belonging to the torso alone.
            transforms[roles.head] = RegionTransform(offset=(0, bob_y))

        frames.append(
            FrameSpec(duration_ms=duration_ms, events=[], transforms=transforms)
        )
    return frames


def generate_procedural_frames(
    doc: SpriteAssetBase, procedural: ProceduralAnimationSpec
) -> list[FrameSpec]:
    """Dispatch a procedural animation declaration to its generator."""
    if procedural.shader == "walk_cycle":
        return generate_walk_cycle(doc, procedural.params)
    raise ForgeError(
        f"unknown procedural shader {procedural.shader!r} (supported: 'walk_cycle')"
    )


def resolve_animation_frames(
    doc: SpriteAssetBase, animation: AnimationSpec
) -> list[FrameSpec]:
    """The concrete frames of an animation: hand-authored frames win; a
    procedural shader supplies them when the frames list is empty. This is the
    single funnel the resolver (and parse-time materialisation) use, so both
    paths agree on the same frames."""
    if animation.frames:
        return animation.frames
    if animation.procedural is None:
        raise ForgeError("animation has no frames and no procedural shader")
    return generate_procedural_frames(doc, animation.procedural)
