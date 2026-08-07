"""Layered transform animation: keyframe interpolation for procedural/prop tracks.

Deterministic easing curves
---------------------------
Every easing curve is a pure function ``[0, 1] -> float`` with exact endpoints
(``f(0) == 0.0``, ``f(1) == 1.0``): no randomness, no clock, identical output
for identical input. Curves are applied to the *normalised* interpolation
parameter before component rounding, so pixel output stays integer and
reproducible. ``ease_bounce`` is a back-ease overshoot (it rises past ``1.0``
mid-way and settles back down to ``1.0``) — the anticipation/overshoot used for
strikes and impacts. The default is ``ease_linear`` (identity), which keeps
every existing call site and spec byte-identical.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from itertools import pairwise

from pixel_forge.errors import ForgeError
from pixel_forge.schemas import RegionTransform
from pixel_forge.schemas.animation import EasingName, FrameSpec
from pixel_forge.schemas.common import RotateSpec

EasingFn = Callable[[float], float]


def ease_linear(t: float) -> float:
    return t


def ease_in(t: float) -> float:
    """Quadratic ease-in: starts slow, accelerates. ``t * t``."""
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return t * t


def ease_out(t: float) -> float:
    """Quadratic ease-out: starts fast, decelerates. ``1 - (1 - t)^2``."""
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return 1.0 - (1.0 - t) * (1.0 - t)


def ease_in_out(t: float) -> float:
    """Smoothstep ease-in-out: symmetric S-curve, zero slope at both ends."""
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return t * t * (3.0 - 2.0 * t)


def ease_bounce(t: float) -> float:
    """Back-ease overshoot: rises past ``1.0`` (peak ~1.10 at t ~ 0.63) then
    settles back down to exactly ``1.0`` at ``t == 1.0``. Anticipation/overshoot
    for impacts and weapon strikes."""
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    c1 = 1.70158
    c3 = c1 + 1.0
    u = t - 1.0
    return 1.0 + c3 * u * u * u + c1 * u * u


#: Name -> curve registry. The single source of truth for the easing names the
#: schema's ``EasingName`` literal accepts; ``easing_curve`` resolves through it.
EASING_CURVES: dict[EasingName, EasingFn] = {
    "linear": ease_linear,
    "ease_in": ease_in,
    "ease_out": ease_out,
    "ease_in_out": ease_in_out,
    "bounce": ease_bounce,
}


def easing_curve(name: EasingName | None) -> EasingFn:
    """Resolve an easing name to its curve. ``None`` and ``"linear"`` both map to
    the identity curve (default, backward-compatible behaviour)."""
    if name is None:
        return ease_linear
    return EASING_CURVES[name]


@dataclass(frozen=True)
class Keyframe:
    at_ms: int
    transform: RegionTransform


def _round_half_away_from_zero(value: float) -> int:
    if value >= 0:
        return math.floor(value + 0.5)
    return math.ceil(value - 0.5)


def _lerp_component(a: int, b: int, t: float) -> int:
    return _round_half_away_from_zero(a + (b - a) * t)


def _lerp_rotate(
    a: RotateSpec | None, b: RotateSpec | None, t_eased: float, t_clamped: float
) -> RotateSpec | None:
    """Interpolate two rotate specs. The angle lerps linearly (a missing side counts
    as the identity rotation, angle 0); the pivot snaps like `visible`/`color_swap` —
    `a`'s for t < 1.0, `b`'s at t == 1.0. Both sides None stays None."""
    if a is None and b is None:
        return None
    a_angle = a.angle_deg if a is not None else 0.0
    b_angle = b.angle_deg if b is not None else 0.0
    angle = a_angle + (b_angle - a_angle) * t_eased
    if t_clamped >= 1.0:
        pivot = b.pivot if b is not None else None
    else:
        pivot = a.pivot if a is not None else None
    return RotateSpec(angle_deg=angle, pivot=pivot)


def lerp_transform(
    a: RegionTransform,
    b: RegionTransform,
    t: float,
    *,
    easing: EasingFn = ease_linear,
) -> RegionTransform:
    """Interpolate two transforms. `offset`/`scale_size` round half away from zero;
    `visible`/`color_swap` snap to `a` for t < 1.0 and to `b` at t == 1.0; `rotate`
    lerps its angle linearly and snaps its pivot on the same rule (see
    `_lerp_rotate`).

    The easing curve is applied to the clamped parameter first: with
    ``ease_bounce`` the offset overshoots past `b` mid-way and settles back — the
    visible/colour-snap semantics are unaffected (they key off the raw clamped
    parameter, so they still switch exactly at ``t == 1.0``).
    """
    t_clamped = max(0.0, min(1.0, t))
    t_eased = easing(t_clamped)
    offset = (
        _lerp_component(a.offset[0], b.offset[0], t_eased),
        _lerp_component(a.offset[1], b.offset[1], t_eased),
    )
    scale_size = (
        _lerp_component(a.scale_size[0], b.scale_size[0], t_eased),
        _lerp_component(a.scale_size[1], b.scale_size[1], t_eased),
    )
    rotate = _lerp_rotate(a.rotate, b.rotate, t_eased, t_clamped)
    if t_clamped >= 1.0:
        visible = b.visible
        color_swap = dict(b.color_swap)
    else:
        visible = a.visible
        color_swap = dict(a.color_swap)
    return RegionTransform(
        offset=offset,
        visible=visible,
        color_swap=color_swap,
        scale_size=scale_size,
        rotate=rotate,
    )


def sample_timeline(
    keyframes: Sequence[Keyframe],
    at_ms: int,
    *,
    easing: EasingFn = ease_linear,
) -> RegionTransform:
    """Sample a sorted keyframe track at `at_ms`, clamping before the first and
    after the last keyframe. Each segment is interpolated with the given easing
    curve (default linear).
    """
    if not keyframes:
        raise ForgeError("sample_timeline requires at least one keyframe")
    for prev, curr in pairwise(keyframes):
        if curr.at_ms < prev.at_ms:
            raise ForgeError("keyframes must be sorted ascending by at_ms")

    if at_ms <= keyframes[0].at_ms:
        return keyframes[0].transform
    if at_ms >= keyframes[-1].at_ms:
        return keyframes[-1].transform

    for prev, curr in pairwise(keyframes):
        if prev.at_ms <= at_ms <= curr.at_ms:
            span = curr.at_ms - prev.at_ms
            t = 0.0 if span == 0 else (at_ms - prev.at_ms) / span
            return lerp_transform(prev.transform, curr.transform, t, easing=easing)

    raise ForgeError("unable to sample timeline")  # pragma: no cover - unreachable


def bake_timeline(
    keyframes: Sequence[Keyframe], frame_durations: Sequence[int]
) -> list[RegionTransform]:
    """Sample the timeline at each frame's cumulative start time."""
    result: list[RegionTransform] = []
    cumulative_ms = 0
    for duration_ms in frame_durations:
        result.append(sample_timeline(keyframes, cumulative_ms))
        cumulative_ms += duration_ms
    return result


def _frame_transform(frame: FrameSpec, region: str) -> RegionTransform:
    # A frame that does not mention a region contributes the identity transform,
    # matching `animation.resolver.merge_transforms`'s convention for missing
    # entries.
    return frame.transforms.get(region, RegionTransform())


def sample_frames(frames: Sequence[FrameSpec], at_ms: int) -> dict[str, RegionTransform]:
    """Sample a `FrameSpec` track at `at_ms`, interpolating every region's
    transform across the frames (treated as keyframes at their cumulative start
    times), and return ``{region: transform}`` for the union of all regions.

    Per-frame animation-quality fields are honoured:
    - the segment leading *into* frame ``i`` (from ``i - 1``'s pose) is
      interpolated with ``frame i``'s ``easing`` curve (default linear), so a
      frame can request the curve for its own offset interpolation;
    - ``hold=True`` on the target frame snaps to its pose for the whole segment
      instead of interpolating (discrete pose change, then hold).

    Times before the first frame's start and after the last frame's end clamp to
    the nearest pose, like `sample_timeline`.
    """
    if not frames:
        raise ForgeError("sample_frames requires at least one frame")

    regions: list[str] = []
    for frame in frames:
        for region in frame.transforms:
            if region not in regions:
                regions.append(region)

    starts: list[int] = []
    cumulative = 0
    for frame in frames:
        starts.append(cumulative)
        cumulative += frame.duration_ms
    end_ms = cumulative

    def pose(index: int) -> dict[str, RegionTransform]:
        return {region: _frame_transform(frames[index], region) for region in regions}

    if at_ms <= starts[0]:
        return pose(0)
    if at_ms >= end_ms:
        return pose(len(frames) - 1)

    for i in range(1, len(frames)):
        seg_start, seg_end = starts[i - 1], starts[i]
        if seg_start <= at_ms < seg_end:
            target = frames[i]
            if target.hold:
                return pose(i)
            span = seg_end - seg_start
            t = 0.0 if span == 0 else (at_ms - seg_start) / span
            curve = easing_curve(target.easing)
            return {
                region: lerp_transform(
                    _frame_transform(frames[i - 1], region),
                    _frame_transform(target, region),
                    t,
                    easing=curve,
                )
                for region in regions
            }
    return pose(len(frames) - 1)  # at_ms inside the last frame's own window


def resample_frames(frames: Sequence[FrameSpec], samples_per_frame: int) -> list[FrameSpec]:
    """Densify a `FrameSpec` track for eased rendering.

    Each authored frame's window is sampled ``samples_per_frame`` times (its own
    start plus ``samples_per_frame - 1`` interior samples) through `sample_frames`,
    so per-frame ``easing`` curves and ``hold`` semantics shape the intermediate
    poses. Sub-frame durations split the authored duration evenly (remainder on
    the last sub-frame), so the total duration is preserved exactly.

    Sub-frame 0 of every window is the authored pose itself (``easing(0) == 0``
    and a ``hold`` target snaps at its segment start, both by `sample_frames`
    semantics), so a linear/default track resampled this way reads the authored
    poses back at the same times — the densification only adds motion where a
    track declared easing or hold.
    """
    if not frames:
        raise ForgeError("resample_frames requires at least one frame")
    if samples_per_frame < 1:
        raise ForgeError("resample_frames: samples_per_frame must be >= 1")

    starts: list[int] = []
    cumulative = 0
    for frame in frames:
        starts.append(cumulative)
        cumulative += frame.duration_ms

    result: list[FrameSpec] = []
    for i, frame in enumerate(frames):
        duration = frame.duration_ms
        base, remainder = divmod(duration, samples_per_frame)
        for k in range(samples_per_frame):
            at_ms = starts[i] + _round_half_away_from_zero(k * duration / samples_per_frame)
            sub_duration = base + (1 if k < remainder else 0)
            result.append(
                FrameSpec(
                    duration_ms=sub_duration,
                    events=[],
                    transforms=sample_frames(frames, at_ms),
                    easing=None,
                    hold=False,
                )
            )
    return result
