"""Deterministic easing curves and per-frame easing in timeline sampling."""

from __future__ import annotations

from itertools import pairwise

import pytest

from pixel_forge.animation import (
    EASING_CURVES,
    Keyframe,
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
from pixel_forge.errors import ForgeError
from pixel_forge.schemas import FrameSpec, RegionTransform


def _samples(curve, steps: int = 100) -> list[float]:
    return [curve(i / steps) for i in range(steps + 1)]


# ---- easing curves: contract ---------------------------------------------------


def test_easing_endpoints_exact() -> None:
    for name, curve in EASING_CURVES.items():
        assert curve(0.0) == 0.0, name
        assert curve(1.0) == 1.0, name


def test_easing_curves_deterministic() -> None:
    for name, curve in EASING_CURVES.items():
        for t in (0.0, 0.1, 0.37, 0.5, 0.999, 1.0):
            assert curve(t) == curve(t), name


def test_easing_smooth_curves_monotonic() -> None:
    for name in ("linear", "ease_in", "ease_out", "ease_in_out"):
        samples = _samples(EASING_CURVES[name])
        assert all(b >= a for a, b in pairwise(samples)), name


def test_ease_bounce_overshoots_then_settles() -> None:
    samples = _samples(ease_bounce, steps=1000)
    assert max(samples) > 1.0  # anticipation/overshoot past the endpoint
    assert ease_bounce(0.0) == 0.0
    assert ease_bounce(1.0) == 1.0
    assert all(v >= 0.0 for v in samples)  # never dips below the start


def test_ease_in_out_midpoint_is_half() -> None:
    assert ease_in_out(0.5) == 0.5
    assert ease_in(0.5) < 0.5 < ease_out(0.5)


def test_easing_curve_resolution() -> None:
    assert easing_curve(None) is ease_linear
    assert easing_curve("linear") is ease_linear
    assert easing_curve("ease_in") is ease_in
    assert easing_curve("ease_out") is ease_out
    assert easing_curve("ease_in_out") is ease_in_out
    assert easing_curve("bounce") is ease_bounce


def test_unknown_easing_name_rejected_by_schema() -> None:
    with pytest.raises(ValueError):
        FrameSpec(duration_ms=100, easing="warp")  # type: ignore[arg-type]


# ---- lerp_transform with easing -------------------------------------------------


def test_lerp_transform_default_is_linear() -> None:
    a = RegionTransform(offset=(-3, 7), scale_size=(1, -2))
    b = RegionTransform(offset=(4, -9), scale_size=(-2, 3))
    for t in (0.0, 0.25, 0.5, 0.75, 1.0):
        assert lerp_transform(a, b, t) == lerp_transform(a, b, t, easing=ease_linear)


def test_lerp_transform_ease_in_midpoint() -> None:
    a = RegionTransform(offset=(0, 0))
    b = RegionTransform(offset=(10, 0))
    # ease_in(0.5) == 0.25 -> 10 * 0.25 == 2.5 -> rounds half away from zero to 3
    assert lerp_transform(a, b, 0.5, easing=ease_in).offset == (3, 0)
    assert lerp_transform(a, b, 0.5).offset == (5, 0)  # linear control


def test_lerp_transform_ease_bounce_overshoots_past_endpoint() -> None:
    a = RegionTransform(offset=(0, 0))
    b = RegionTransform(offset=(10, 0))
    mid = lerp_transform(a, b, 0.5, easing=ease_bounce)
    assert mid.offset[0] > 10  # 10 * ~1.0877 -> 11


def test_lerp_transform_easing_keeps_visible_snap() -> None:
    a = RegionTransform(visible=True)
    b = RegionTransform(visible=False)
    # visible snaps on the raw parameter, not the (overshooting) eased one
    assert lerp_transform(a, b, 0.95, easing=ease_bounce).visible is True
    assert lerp_transform(a, b, 1.0, easing=ease_bounce).visible is False


# ---- sample_timeline with easing -------------------------------------------------


def test_sample_timeline_easing_shapes_interior() -> None:
    keyframes = [
        Keyframe(0, RegionTransform(offset=(0, 0))),
        Keyframe(100, RegionTransform(offset=(10, 0))),
    ]
    assert sample_timeline(keyframes, 50, easing=ease_in).offset == (3, 0)
    assert sample_timeline(keyframes, 50).offset == (5, 0)


def test_sample_timeline_easing_still_clamps_ends() -> None:
    keyframes = [
        Keyframe(0, RegionTransform(offset=(0, 0))),
        Keyframe(100, RegionTransform(offset=(10, 0))),
    ]
    assert sample_timeline(keyframes, -5, easing=ease_bounce).offset == (0, 0)
    assert sample_timeline(keyframes, 500, easing=ease_bounce).offset == (10, 0)


def test_sample_timeline_default_matches_explicit_linear() -> None:
    keyframes = [
        Keyframe(0, RegionTransform(offset=(-3, 7))),
        Keyframe(100, RegionTransform(offset=(4, -9))),
    ]
    for at_ms in (0, 13, 50, 99, 100):
        assert sample_timeline(keyframes, at_ms) == sample_timeline(
            keyframes, at_ms, easing=ease_linear
        )


# ---- sample_frames: per-frame easing + hold over a FrameSpec track ---------------


def test_sample_frames_linear_between_poses() -> None:
    frames = [
        FrameSpec(duration_ms=100, transforms={"body": RegionTransform(offset=(0, 0))}),
        FrameSpec(duration_ms=100, transforms={"body": RegionTransform(offset=(10, 0))}),
    ]
    assert sample_frames(frames, 0)["body"].offset == (0, 0)
    assert sample_frames(frames, 50)["body"].offset == (5, 0)
    assert sample_frames(frames, 100)["body"].offset == (10, 0)
    assert sample_frames(frames, 250)["body"].offset == (10, 0)  # clamped past the end


def test_sample_frames_per_frame_easing() -> None:
    frames = [
        FrameSpec(duration_ms=100, transforms={"body": RegionTransform(offset=(0, 0))}),
        FrameSpec(
            duration_ms=100,
            transforms={"body": RegionTransform(offset=(10, 0))},
            easing="ease_in",
        ),
    ]
    # the target frame's easing shapes the segment leading into it
    assert sample_frames(frames, 50)["body"].offset == (3, 0)


def test_sample_frames_hold_snaps() -> None:
    frames = [
        FrameSpec(duration_ms=100, transforms={"body": RegionTransform(offset=(0, 0))}),
        FrameSpec(
            duration_ms=100,
            transforms={"body": RegionTransform(offset=(10, 0))},
            hold=True,
        ),
    ]
    # hold: the pose change happens at the segment start, then holds
    assert sample_frames(frames, 50)["body"].offset == (10, 0)
    assert sample_frames(frames, 99)["body"].offset == (10, 0)


def test_sample_frames_unions_all_regions() -> None:
    frames = [
        FrameSpec(duration_ms=100, transforms={"a": RegionTransform(offset=(0, 0))}),
        FrameSpec(duration_ms=100, transforms={"b": RegionTransform(offset=(4, 0))}),
    ]
    out = sample_frames(frames, 50)
    assert set(out) == {"a", "b"}
    assert out["a"].offset == (0, 0)
    assert out["b"].offset == (2, 0)


def test_sample_frames_bounce_overshoots() -> None:
    frames = [
        FrameSpec(duration_ms=100, transforms={"body": RegionTransform(offset=(0, 0))}),
        FrameSpec(
            duration_ms=100,
            transforms={"body": RegionTransform(offset=(10, 0))},
            easing="bounce",
        ),
    ]
    assert sample_frames(frames, 50)["body"].offset[0] > 10


def test_sample_frames_empty_raises() -> None:
    with pytest.raises(ForgeError):
        sample_frames([], 0)


# ---- resample_frames: easing/hold -> dense sub-frame track ------------------------


def test_resample_frames_preserves_poses_and_duration() -> None:
    frames = [
        FrameSpec(duration_ms=110, transforms={"body": RegionTransform(offset=(0, 0))}),
        FrameSpec(duration_ms=70, transforms={"body": RegionTransform(offset=(0, 4))}),
    ]
    out = resample_frames(frames, 4)
    assert len(out) == 8
    assert sum(f.duration_ms for f in out) == 180  # total duration preserved
    # sub-frame 0 of each window is the authored pose (sampling at the start)
    assert out[0].transforms["body"].offset == (0, 0)
    assert out[4].transforms["body"].offset == (0, 4)
    # sub-frames never carry easing/hold (already baked into the sampled poses)
    assert all(f.easing is None and not f.hold for f in out)


def test_resample_frames_easing_shapes_intermediates() -> None:
    def offsets(easing_name: str) -> list[int]:
        frames = [
            FrameSpec(duration_ms=100, transforms={"body": RegionTransform(offset=(0, 0))}),
            FrameSpec(
                duration_ms=100,
                transforms={"body": RegionTransform(offset=(0, 10))},
                easing=easing_name,  # type: ignore[arg-type]
            ),
        ]
        return [f.transforms["body"].offset[1] for f in resample_frames(frames, 4)]

    linear = offsets("linear")
    eased = offsets("ease_in_out")
    assert linear == [0, 3, 5, 8, 10, 10, 10, 10]
    assert eased[1] < linear[1]  # ease_in_out starts slower than linear
    assert eased != linear
    # smooth: monotonic, no 1-frame snap
    assert all(b >= a for a, b in pairwise(eased))
    assert all(abs(b - a) <= 3 for a, b in pairwise(eased))


def test_resample_frames_bounce_overshoots_past_target() -> None:
    frames = [
        FrameSpec(duration_ms=100, transforms={"body": RegionTransform(offset=(0, 0))}),
        FrameSpec(
            duration_ms=100,
            transforms={"body": RegionTransform(offset=(0, 10))},
            easing="bounce",
        ),
    ]
    out = resample_frames(frames, 4)
    # sub-frame 2 of the first window sits at t=0.5 where ease_bounce ~ 1.088
    assert out[2].transforms["body"].offset[1] == 11  # overshoots past the endpoint
    assert out[0].transforms["body"].offset[1] == 0
    assert out[4].transforms["body"].offset[1] == 10  # target pose at its own start


def test_resample_frames_hold_snaps_to_target() -> None:
    frames = [
        FrameSpec(duration_ms=100, transforms={"body": RegionTransform(offset=(0, 0))}),
        FrameSpec(
            duration_ms=100,
            transforms={"body": RegionTransform(offset=(0, 10))},
            hold=True,
        ),
    ]
    out = resample_frames(frames, 4)
    # hold snaps at its segment start and holds the whole window (sample_frames
    # semantics); sub-frame 0 of the first window is still the authored pose, so
    # the resampled track is: authored pose, then the snapped target holding
    assert [f.transforms["body"].offset[1] for f in out] == [0] + [10] * 7


def test_resample_frames_linear_matches_authored_poses() -> None:
    # A default/linear track resampled this way reads the authored poses back at
    # the same times — the densification adds no motion by itself (this is what
    # keeps existing specs byte-identical: no easing, no sub-frames).
    frames = [
        FrameSpec(duration_ms=100, transforms={"body": RegionTransform(offset=(0, 0))}),
        FrameSpec(duration_ms=100, transforms={"body": RegionTransform(offset=(0, 10))}),
    ]
    out = resample_frames(frames, 4)
    assert out[0].transforms["body"].offset == (0, 0)
    assert out[4].transforms["body"].offset == (0, 10)
    assert out[7].transforms["body"].offset == (0, 10)  # last window holds its pose


def test_resample_frames_invalid_args() -> None:
    with pytest.raises(ForgeError):
        resample_frames([], 4)
    with pytest.raises(ForgeError):
        resample_frames([FrameSpec(duration_ms=100)], 0)
