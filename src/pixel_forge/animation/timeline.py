"""Layered transform animation: keyframe interpolation for procedural/prop tracks."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from pixel_forge.errors import ForgeError
from pixel_forge.schemas import RegionTransform


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


def lerp_transform(a: RegionTransform, b: RegionTransform, t: float) -> RegionTransform:
    """Interpolate two transforms. `offset`/`scale_size` round half away from zero;
    `visible`/`color_swap` snap to `a` for t < 1.0 and to `b` at t == 1.0.
    """
    t_clamped = max(0.0, min(1.0, t))
    offset = (
        _lerp_component(a.offset[0], b.offset[0], t_clamped),
        _lerp_component(a.offset[1], b.offset[1], t_clamped),
    )
    scale_size = (
        _lerp_component(a.scale_size[0], b.scale_size[0], t_clamped),
        _lerp_component(a.scale_size[1], b.scale_size[1], t_clamped),
    )
    if t_clamped >= 1.0:
        visible = b.visible
        color_swap = dict(b.color_swap)
    else:
        visible = a.visible
        color_swap = dict(a.color_swap)
    return RegionTransform(
        offset=offset, visible=visible, color_swap=color_swap, scale_size=scale_size
    )


def sample_timeline(keyframes: Sequence[Keyframe], at_ms: int) -> RegionTransform:
    """Sample a sorted keyframe track at `at_ms`, clamping before the first and
    after the last keyframe.
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
            return lerp_transform(prev.transform, curr.transform, t)

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
