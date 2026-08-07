"""Procedural walk-cycle generator: determinism, symmetry, anchor stability."""

from __future__ import annotations

from itertools import pairwise
from typing import Any

import numpy as np
import pytest

from pixel_forge.animation import (
    animation_duration_ms,
    generate_procedural_frames,
    resolve_frames,
)
from pixel_forge.errors import ForgeError
from pixel_forge.rendering.local import render_asset_frames
from pixel_forge.schemas import ProceduralAnimationSpec, parse_asset_doc

_REGIONS: dict[str, Any] = {
    "shadow": {
        "anchor": "feet",
        "layer": 0,
        "shapes": [{"op": "ellipse", "color": "s", "at": [-10, -2], "size": [20, 3]}],
    },
    "body": {
        "anchor": "torso",
        "layer": 10,
        "shapes": [{"op": "rect", "color": "c", "at": [-7, -8], "size": [14, 14]}],
    },
    "leg_L": {
        "anchor": "hip_l",
        "layer": 5,
        "shapes": [{"op": "rect", "color": "c", "at": [-2, 0], "size": [4, 6]}],
    },
    "leg_R": {
        "anchor": "hip_r",
        "layer": 5,
        "shapes": [{"op": "rect", "color": "c", "at": [-2, 0], "size": [4, 6]}],
    },
    "arm_L": {
        "anchor": "shoulder_l",
        "layer": 15,
        "shapes": [{"op": "rect", "color": "c", "at": [-1, 0], "size": [3, 8]}],
    },
    "arm_R": {
        "anchor": "shoulder_r",
        "layer": 15,
        "shapes": [{"op": "rect", "color": "c", "at": [-2, 0], "size": [3, 8]}],
    },
    "head": {
        "anchor": "head",
        "layer": 20,
        "shapes": [{"op": "rect", "color": "c", "at": [-4, -6], "size": [8, 8]}],
    },
}

_ANCHORS: dict[str, Any] = {
    "feet": [16, 30],
    "torso": [16, 18],
    "hip_l": [13, 22],
    "hip_r": [19, 22],
    "shoulder_l": [10, 20],
    "shoulder_r": [22, 20],
    "head": [16, 8],
}


def _walk_doc(
    *,
    regions: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    animation: dict[str, Any] | None = None,
) -> Any:
    walk: dict[str, Any] = {
        "loop": True,
        "frames": [],
        "procedural": {"shader": "walk_cycle", "params": params or {}},
    }
    if animation is not None:
        walk = animation
    return parse_asset_doc(
        {
            "schema_version": 1,
            "asset": {
                "id": "scout",
                "type": "character",
                "canvas": [32, 32],
                "baseline_y": 30,
            },
            "palette": {
                "id": "p",
                "colors": [
                    {"id": "c", "hex": "#ffffff"},
                    {"id": "s", "hex": "#000000"},
                ],
            },
            "directions": ["south"],
            "anchors": _ANCHORS,
            "regions": _REGIONS if regions is None else regions,
            "animations": {"walk": walk},
            "export": {},
            "validation": {},
        }
    )


# ---- generation contract ---------------------------------------------------------


def test_walk_cycle_materialized_at_parse() -> None:
    doc = _walk_doc()
    frames = doc.animations["walk"].frames
    assert len(frames) == 8
    assert all(f.duration_ms == 110 for f in frames)  # 90-150ms quality bar
    assert all(f.events == [] for f in frames)


def test_walk_cycle_resolves_through_resolver() -> None:
    doc = _walk_doc()
    resolved = resolve_frames(doc)
    assert len(resolved) == 8
    assert all(r.animation == "walk" for r in resolved)
    assert [r.duration_ms for r in resolved] == [110] * 8


def test_walk_cycle_generator_is_deterministic() -> None:
    doc = _walk_doc()
    first = generate_procedural_frames(
        doc, ProceduralAnimationSpec(shader="walk_cycle", params={"frames": 8})
    )
    second = generate_procedural_frames(
        doc, ProceduralAnimationSpec(shader="walk_cycle", params={"frames": 8})
    )
    assert [f.transforms for f in first] == [f.transforms for f in second]


# ---- quality invariants -----------------------------------------------------------


def test_walk_cycle_foot_anchor_stable() -> None:
    doc = _walk_doc()
    for frame in doc.animations["walk"].frames:
        assert "shadow" not in frame.transforms
    resolved = resolve_frames(doc)
    assert all(r.transforms["shadow"].offset == (0, 0) for r in resolved)


def test_walk_cycle_left_right_symmetry() -> None:
    doc = _walk_doc()
    for frame in doc.animations["walk"].frames:
        assert frame.transforms["leg_L"].offset[0] == -frame.transforms["leg_R"].offset[0]
        assert frame.transforms["arm_L"].offset[0] == -frame.transforms["arm_R"].offset[0]


def test_walk_cycle_legs_swing_and_alternate() -> None:
    doc = _walk_doc(params={"swing": 2})
    leg_xs = [f.transforms["leg_L"].offset[0] for f in doc.animations["walk"].frames]
    assert max(leg_xs) == 2
    assert leg_xs[0] > 0 and leg_xs[4] < 0  # left forward at contact 0, back at contact 4


def test_walk_cycle_bob_present_and_up_only() -> None:
    doc = _walk_doc()
    ys = [f.transforms["body"].offset[1] for f in doc.animations["walk"].frames]
    assert max(ys) == 0
    assert min(ys) == -1  # bob amplitude 1px, never below rest (baseline-safe)
    assert len(set(ys)) >= 2  # the body actually moves


def test_walk_cycle_arms_counter_swing() -> None:
    doc = _walk_doc(params={"swing": 2, "arm_swing": 2})
    for frame in doc.animations["walk"].frames:
        arm = frame.transforms["arm_L"].offset[0]
        leg = frame.transforms["leg_L"].offset[0]
        assert arm == -leg  # arms oppose the legs on the same side


def test_walk_cycle_loop_closes() -> None:
    # The generator is 2pi-periodic in phase, so the pose after the last frame
    # (phase 2pi) equals the pose at frame 0 (phase 0) and the loop closes
    # without a pose jump. Verified structurally: a 16-frame cycle reproduces
    # the 8-frame cycle exactly at even indices (identical phases). The wrap
    # step's pixel size is asserted in test_walk_cycle_no_contact_snap.
    doc = _walk_doc()
    eight = doc.animations["walk"].frames
    sixteen = generate_procedural_frames(
        doc, ProceduralAnimationSpec(shader="walk_cycle", params={"frames": 16})
    )
    for i in range(8):
        assert sixteen[2 * i].transforms == eight[i].transforms


def test_walk_cycle_foot_lift_anti_phased() -> None:
    doc = _walk_doc(params={"swing": 2, "lift": 2})
    frames = doc.animations["walk"].frames
    # contact frames (0, 4): both feet planted at the same height (bob 0)
    for i in (0, 4):
        assert frames[i].transforms["leg_L"].offset[1] == 0
        assert frames[i].transforms["leg_R"].offset[1] == 0
    # passing frames (2, 6): the swing leg lifts *relative to the shared bob* —
    # the stance leg rides only the bob, the swing leg lifts on top of it
    assert frames[2].transforms["leg_L"].offset[1] == frames[2].transforms["body"].offset[1]
    assert frames[2].transforms["leg_R"].offset[1] < frames[2].transforms["leg_L"].offset[1]
    assert frames[6].transforms["leg_R"].offset[1] == frames[6].transforms["body"].offset[1]
    assert frames[6].transforms["leg_L"].offset[1] < frames[6].transforms["leg_R"].offset[1]
    # every leg lifts beyond the shared bob at some point: no more shuffle
    assert any(
        f.transforms["leg_L"].offset[1] < f.transforms["body"].offset[1] for f in frames
    )
    assert any(
        f.transforms["leg_R"].offset[1] < f.transforms["body"].offset[1] for f in frames
    )


def test_walk_cycle_params_respected() -> None:
    doc = _walk_doc(params={"swing": 2, "bob": 0, "arm_swing": 3, "duration_ms": 90})
    frames = doc.animations["walk"].frames
    assert all(f.duration_ms == 90 for f in frames)
    assert max(abs(f.transforms["leg_L"].offset[0]) for f in frames) == 2
    assert max(abs(f.transforms["arm_L"].offset[0]) for f in frames) == 3
    assert all(f.transforms["body"].offset[1] == 0 for f in frames)


def test_walk_cycle_squash_optional() -> None:
    plain = _walk_doc()
    assert all(
        f.transforms["body"].scale_size == (0, 0) for f in plain.animations["walk"].frames
    )
    squashed = _walk_doc(params={"squash": True})
    scales = {f.transforms["body"].scale_size for f in squashed.animations["walk"].frames}
    # smooth contact squash (-1 vertical) with a volume-preserving horizontal
    # counter-scale (+1), relaxed at passing — no 1-frame -1/+1 snap, no stretch
    assert scales == {(1, -1), (0, 0)}
    v_sequence = [
        f.transforms["body"].scale_size[1] for f in squashed.animations["walk"].frames
    ]
    assert all(abs(b - a) <= 1 for a, b in pairwise(v_sequence))


def test_walk_cycle_invalid_params_rejected() -> None:
    with pytest.raises(ForgeError, match="frames"):
        _walk_doc(params={"frames": 1})
    with pytest.raises(ForgeError, match="duration_ms"):
        _walk_doc(params={"duration_ms": 0})
    with pytest.raises(ForgeError, match="bob"):
        _walk_doc(params={"bob": -1})


def test_walk_cycle_unknown_shader_raises() -> None:
    doc = _walk_doc()
    with pytest.raises(ForgeError, match="unknown procedural shader"):
        generate_procedural_frames(doc, ProceduralAnimationSpec(shader="moonwalk", params={}))


def test_walk_cycle_body_only_doc_falls_back_to_bob() -> None:
    body_only = {"shadow": _REGIONS["shadow"], "body": _REGIONS["body"]}
    doc = _walk_doc(regions=body_only)
    frames = doc.animations["walk"].frames
    assert all(set(f.transforms) == {"body"} for f in frames)
    assert len({f.transforms["body"].offset[1] for f in frames}) >= 2


# ---- wiring through the schema ---------------------------------------------------


def test_animation_requires_frames_without_procedural() -> None:
    with pytest.raises(ValueError, match="at least 1 frame"):
        _walk_doc(animation={"loop": True, "frames": []})


def test_hand_authored_frames_win_over_procedural() -> None:
    doc = _walk_doc(
        animation={
            "loop": True,
            "frames": [{"duration_ms": 200, "transforms": {}}],
            "procedural": {"shader": "walk_cycle", "params": {"frames": 8}},
        }
    )
    assert len(doc.animations["walk"].frames) == 1
    assert doc.animations["walk"].frames[0].duration_ms == 200


def test_programmatic_doc_regenerates_in_resolver() -> None:
    # Simulate a doc built without parse_asset_doc (frames never materialized):
    # the resolver's fallback must still produce frames.
    doc = _walk_doc()
    doc.animations["walk"].frames = []
    resolved = resolve_frames(doc)
    assert len(resolved) == 8
    assert animation_duration_ms(doc, "walk") == 8 * 110


def test_easing_and_hold_surface_on_resolved_frame() -> None:
    doc = _walk_doc(
        animation={
            "loop": True,
            "frames": [
                {
                    "duration_ms": 100,
                    "transforms": {},
                    "easing": "ease_out",
                    "hold": True,
                }
            ],
        }
    )
    frame = resolve_frames(doc)[0]
    assert frame.easing == "ease_out"
    assert frame.hold is True
    plain = resolve_frames(_walk_doc())[0]
    assert plain.easing is None
    assert plain.hold is False


def test_walk_cycle_renders_and_moves() -> None:
    doc = _walk_doc()
    rendered = render_asset_frames(doc)
    assert len(rendered) == 8
    arrays = [canvas.array for canvas in rendered.values()]
    consecutive_diffs = [
        int(np.count_nonzero(arrays[i] != arrays[i + 1])) for i in range(len(arrays) - 1)
    ]
    assert all(d > 0 for d in consecutive_diffs)
    assert len({arr.tobytes() for arr in arrays}) >= 4  # several distinct poses


# ---- believability regressions (critic round 2) ---------------------------------------------


def _non_shadow_mask(arr: np.ndarray) -> np.ndarray:
    """Opaque pixels that are not the test doc's shadow colour ('s' = #000000)."""
    shadow_rgba = np.array([0, 0, 0, 255], dtype=np.uint8)
    opaque = arr[..., 3] != 0
    return opaque & ~np.all(arr == shadow_rgba, axis=-1)


def _top_row(arr: np.ndarray) -> int:
    return int(np.min(np.argwhere(arr[..., 3] != 0)[:, 0]))


def _bottom_row(arr: np.ndarray) -> int:
    rows = np.argwhere(_non_shadow_mask(arr))[:, 0]
    return int(np.max(rows))


def test_walk_cycle_head_rides_the_bob() -> None:
    # Regression (critic's biggest gap): the discovered head region must be
    # driven with the same bob as the body so the upper body moves as one mass —
    # the rendered head top row must move across frames instead of staying
    # pixel-static while the torso pumps.
    doc = _walk_doc(params={"bob": 2})
    for frame in doc.animations["walk"].frames:
        assert frame.transforms["head"].offset[1] == frame.transforms["body"].offset[1]
    rendered = render_asset_frames(doc)
    top_rows = [_top_row(canvas.array) for canvas in rendered.values()]
    assert len(set(top_rows)) >= 2  # the head top row actually moves
    assert top_rows[0] != top_rows[1]  # not static at the very first transition


def test_walk_cycle_feet_lift_in_rendered_frames() -> None:
    # Regression: the character bottom row used to be pixel-static at y=27 in
    # every frame (both feet always planted -> shuffle). The swing foot must
    # lift, so the bottom row alternates by >= 1px across the cycle.
    doc = _walk_doc(params={"swing": 2, "lift": 2, "bob": 2})
    rendered = render_asset_frames(doc)
    bottoms = [_bottom_row(canvas.array) for canvas in rendered.values()]
    assert len(set(bottoms)) >= 2
    assert max(bottoms) - min(bottoms) >= 1
    # and the lifts are anti-phased: at the passing frames the swing leg is
    # lifted relative to the stance leg (which rides only the shared bob)
    frames = doc.animations["walk"].frames
    assert frames[2].transforms["leg_R"].offset[1] < frames[2].transforms["leg_L"].offset[1]
    assert frames[6].transforms["leg_L"].offset[1] < frames[6].transforms["leg_R"].offset[1]


def test_walk_cycle_no_contact_snap() -> None:
    # Regression: contact squash used to be a 1-frame ~4px snap (body drops bob 0
    # + squashes -1 in one step, then rises + stretches +1). Now every
    # consecutive-frame change of the rendered top/bottom row is <= 2px and the
    # extent (height) never changes by >= 3px in one step, loop wrap included.
    doc = _walk_doc(params={"bob": 2, "squash": True, "swing": 2, "lift": 2})
    rendered = render_asset_frames(doc)
    tops = [_top_row(canvas.array) for canvas in rendered.values()]
    bottoms = [_bottom_row(canvas.array) for canvas in rendered.values()]
    n = len(tops)
    for i in range(n):
        j = (i + 1) % n  # include the loop wrap
        assert abs(tops[j] - tops[i]) <= 2
        assert abs(bottoms[j] - bottoms[i]) <= 2
        extent = bottoms[j] - tops[j]
        prev_extent = bottoms[i] - tops[i]
        assert abs(extent - prev_extent) <= 3  # no 4px 1-frame snap


def test_walk_cycle_head_top_no_passing_pop() -> None:
    # Regression (critic round 2, P4 nit): the head offset used to stack
    # bob_y + squash_shift, and at passing both terms dropped in the same frame
    # (bob -1 while the squash-shift relaxed 1 -> 0), so the rendered head top
    # jumped 2px in one frame (... 2, 0, 2 ...) — exactly AT the <= 2px bar.
    # The head now rides the bob only: every consecutive head-top step, loop
    # wrap included, is <= 1px.
    doc = _walk_doc(params={"bob": 2, "squash": True, "swing": 2, "lift": 2})
    frames = doc.animations["walk"].frames
    # structural: the head transform is exactly the bob — no squash stacking
    for frame in frames:
        assert frame.transforms["head"].offset[1] == frame.transforms["body"].offset[1]
    rendered = render_asset_frames(doc)
    tops = [_top_row(canvas.array) for canvas in rendered.values()]
    n = len(tops)
    for i in range(n):
        j = (i + 1) % n  # include the loop wrap
        assert abs(tops[j] - tops[i]) <= 1


def test_eased_frames_render_smoothed_motion() -> None:
    # Regression: easing/hold used to be inert — nothing consumed them. Now a
    # track declaring per-frame easing gets eased sub-frames out of
    # render_asset_frames, and the easing curve reshapes the rendered
    # intermediates (ease_in_out is slower at the start than linear).
    plain = render_asset_frames(_walk_doc())
    assert all(len(key) == 3 for key in plain)  # no easing -> authored frames only

    linear_doc = _walk_doc(
        animation={
            "loop": True,
            "frames": [
                {"duration_ms": 100, "transforms": {"body": {"offset": [0, 0]}}},
                {
                    "duration_ms": 100,
                    "transforms": {"body": {"offset": [0, 10]}},
                    "easing": "linear",
                },
            ],
        }
    )
    eased_doc = _walk_doc(
        animation={
            "loop": True,
            "frames": [
                {"duration_ms": 100, "transforms": {"body": {"offset": [0, 0]}}},
                {
                    "duration_ms": 100,
                    "transforms": {"body": {"offset": [0, 10]}},
                    "easing": "ease_in_out",
                },
            ],
        }
    )
    linear = render_asset_frames(linear_doc)
    eased = render_asset_frames(eased_doc)
    sub_keys = [key for key in eased if len(key) == 4]
    assert len(sub_keys) == 2 * 3  # 2 authored frames x (4 samples - 1)
    # authored keys are byte-identical to un-eased rendering (linear == eased at
    # the frame starts), only the interior samples differ
    assert linear[("walk", "south", 0)].array.tobytes() == eased[
        ("walk", "south", 0)
    ].array.tobytes()
    sub_linear = linear[("walk", "south", 0, 1)]
    sub_eased = eased[("walk", "south", 0, 1)]
    assert sub_linear.array.tobytes() != sub_eased.array.tobytes()
