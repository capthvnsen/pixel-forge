"""Geometry-aware joint-walk swing clamps: thin long limbs keep their feet
from crossing into X-blobs at extreme stride, and arms keep their hands from
reaching the canvas edges (flailing), while thick/short limbs and
unmeasurable limb pairs keep the full requested swing.

Regression for the coherence-demo defects: at the default ±35° joint swing the
demo character's 3px-wide legs (4px boot row 12px below the hip, 9px hip gap)
cross into a fused boot blob at peak stride, and at the 0.6x arm counter-swing
the hands (resting ~2-3px from the canvas edges) reach x=0/x=30 and read as
flailing. Both clamps are pure functions of the doc — joint gap, limb length
below the pivot, limb width, boot width, and the hand's distance from the
canvas edge — deterministic, and never crash on art they cannot measure.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from pixel_forge.animation.cycles import generate_joint_walk_cycle
from pixel_forge.domain.palette import resolve_palette
from pixel_forge.errors import ForgeError
from pixel_forge.rendering.canvas import Canvas
from pixel_forge.rendering.compositor import composite, plan_layers
from pixel_forge.schemas import parse_asset_doc
from pixel_forge.schemas.animation import FrameSpec
from pixel_forge.schemas.asset import SpriteAssetBase
from pixel_forge.schemas.common import RotateSpec

_PALETTE = [
    {"id": "ink", "hex": "#1a1512"},
    {"id": "pants_l", "hex": "#4a4a5a"},
    {"id": "pants_r", "hex": "#5a5a6a"},
    {"id": "sleeve_l", "hex": "#3a9e5a"},
    {"id": "sleeve_r", "hex": "#b04a4a"},
]


def _bitmap(at: tuple[int, int], key: dict[str, str], rows: list[str]) -> dict[str, object]:
    return {"op": "bitmap", "at": list(at), "key": key, "rows": rows}


def _leg_regions(rows: list[str]) -> dict[str, Any]:
    """Demo-like leg regions: bitmaps hanging straight down from the hip pivot."""
    right_rows = ["".join("R" if ch == "L" else ch for ch in row) for row in rows]
    return {
        "leg_left": {
            "anchor": "hip_l",
            "layer": 5,
            "shapes": [_bitmap((-1, 0), {"o": "ink", "L": "pants_l"}, rows)],
        },
        "leg_right": {
            "anchor": "hip_r",
            "layer": 5,
            "shapes": [_bitmap((-1, 0), {"o": "ink", "R": "pants_r"}, right_rows)],
        },
    }


def _arm_regions(rows: list[str]) -> dict[str, Any]:
    right_rows = ["".join("B" if ch == "A" else ch for ch in row) for row in rows]
    return {
        "arm_left": {
            "anchor": "shoulder_l",
            "layer": 8,
            "shapes": [_bitmap((-1, 0), {"o": "ink", "A": "sleeve_l"}, rows)],
        },
        "arm_right": {
            "anchor": "shoulder_r",
            "layer": 8,
            "shapes": [_bitmap((-1, 0), {"o": "ink", "B": "sleeve_r"}, right_rows)],
        },
    }


def _make_doc(
    *,
    leg_rows: list[str] | None = None,
    hip_gap: int = 9,
    arm_rows: list[str] | None = None,
    legs: dict[str, Any] | None = None,
    arms: dict[str, Any] | None = None,
    anchors: dict[str, Any] | None = None,
) -> SpriteAssetBase:
    """A demo-character-like doc (canvas 31x51, hips at y=34, 3px limbs).

    Defaults: legs 3px wide x 14px below the hip pivot with a 9px hip gap
    (the coherence-demo ground truth), arms 3px wide x 10px below the
    shoulder pivot with a 21px shoulder gap — so the shoulder-gap scissor
    clamp stays silent, but the right hand rests only 3px from the canvas
    edge, which is what the canvas-edge arm clamp measures.
    """
    if leg_rows is None:
        leg_rows = ["oLo"] * 15  # 3px wide; lowest pixel 14px below the pivot
    if arm_rows is None:
        arm_rows = ["AAo"] * 11  # 3px wide; lowest pixel 10px below the pivot
    regions: dict[str, Any] = {}
    regions.update(_leg_regions(leg_rows) if legs is None else legs)
    regions.update(_arm_regions(arm_rows) if arms is None else arms)
    default_anchors: dict[str, Any] = {
        "hip_l": [12, 34],
        "hip_r": [12 + hip_gap, 34],
        "shoulder_l": [5, 15],
        "shoulder_r": [26, 15],
    }
    doc = parse_asset_doc(
        {
            "schema_version": 1,
            "asset": {"id": "hero", "type": "character", "canvas": [31, 51]},
            "palette": {"id": "p", "colors": _PALETTE},
            "directions": ["south"],
            "anchors": default_anchors if anchors is None else anchors,
            "regions": regions,
            "animations": {},
            "export": {},
            "validation": {},
        }
    )
    assert isinstance(doc, SpriteAssetBase)
    return doc


def _rot(frame: FrameSpec, region_name: str) -> RotateSpec:
    rot = frame.transforms[region_name].rotate
    assert rot is not None
    return rot


def _render_leg_pair(doc: SpriteAssetBase, frames: list[FrameSpec]) -> list[Canvas]:
    """Composite ONLY the two legs per frame (region pixels + the frame's
    transforms), so boot-row runs are measured without the torso or arms."""
    palette = resolve_palette(doc.palette)
    legs = {name: doc.regions[name] for name in ("leg_left", "leg_right")}
    width, height = doc.asset.canvas
    out: list[Canvas] = []
    for frame in frames:
        layers = plan_layers(doc, legs, doc.anchors, frame.transforms, palette)
        out.append(composite((width, height), layers, palette))
    return out


def _render_arm_pair(doc: SpriteAssetBase, frames: list[FrameSpec]) -> list[Canvas]:
    """Composite ONLY the two arms per frame, so the hand x-extent is
    measured without the torso or legs (south == front view, so the doc's
    own composite is the projected south frame)."""
    palette = resolve_palette(doc.palette)
    arms = {name: doc.regions[name] for name in ("arm_left", "arm_right")}
    width, height = doc.asset.canvas
    out: list[Canvas] = []
    for frame in frames:
        layers = plan_layers(doc, arms, doc.anchors, frame.transforms, palette)
        out.append(composite((width, height), layers, palette))
    return out


def _opaque_x_range(canvas: Canvas) -> tuple[int, int]:
    """(min_x, max_x) over the canvas's opaque pixels; the hand sweep."""
    a = canvas.array
    _, xs = (a[:, :, 3] > 0).nonzero()
    assert len(xs) > 0
    return (int(xs.min()), int(xs.max()))


def _max_bottom_run(canvas: Canvas, rows: int = 3) -> int:
    """Max contiguous opaque run over the bottom `rows` opaque rows."""
    a = canvas.array
    opaque_rows = [y for y in range(a.shape[0]) if bool((a[y, :, 3] > 0).any())]
    if not opaque_rows:
        return 0
    best = 0
    for y in range(max(0, opaque_rows[-1] - rows + 1), opaque_rows[-1] + 1):
        run = 0
        for x in range(a.shape[1]):
            if a[y, x, 3] > 0:
                run += 1
                best = max(best, run)
            else:
                run = 0
    return best


# --- the clamp fires -----------------------------------------------------------


def test_walk_clamp_fires_on_thin_long_legs() -> None:
    """Demo geometry: at the requested ±35° the boot tips would sweep ~8px
    across the 9px hip gap and read as a fused blob. The clamp must lower the
    peak leg swing to the geometry's safe swing (strictly < 35°)."""
    doc = _make_doc()
    frames = generate_joint_walk_cycle(doc, {})
    max_angle = max(abs(_rot(f, "leg_left").angle_deg) for f in frames)
    # safe swing = atan((gap - boot_width - 2*clearance) / (2*length)) =
    # atan((9 - 3 - 2) / 28) = atan(4/28) = 8.13°, floored at 2° (the old 15°
    # floor would fuse the 3px boots), never above the requested 35°.
    expected_safe = min(
        35.0,
        max(2.0, math.degrees(math.atan((9.0 - 3.0 - 2.0) / (2.0 * 14.0)))),
    )
    assert math.isclose(expected_safe, math.degrees(math.atan(4.0 / 28.0)))
    assert max_angle <= expected_safe
    assert max_angle < 35.0
    # The scissor stays symmetric (equal-and-opposite) under the clamp.
    for f in frames:
        assert _rot(f, "leg_left").angle_deg == -_rot(f, "leg_right").angle_deg
    # Arms keep a clearly-readable counter-swing: with a 21px shoulder gap
    # and 10px arms the hand tips never approach the midline, so the
    # shoulder-gap scissor clamp stays silent; the arm amplitude derives
    # from the REQUESTED swing, never from the clamped leg swing — the
    # round-5 critic's 'no arm bend' complaint was the arms inheriting
    # ~0.6x of the ~7° leg clamp (~4°, invisible). The canvas-edge clamp
    # DOES cap them (the right hand rests 3px from the canvas edge, so
    # asin((3-1)/10) = 11.54° < the requested 21°), but they still swing
    # clearly MORE than the clamped legs.
    arm_max = max(abs(_rot(f, "arm_left").angle_deg) for f in frames)
    expected_arm = math.degrees(math.asin((3.0 - 1.0) / 10.0))
    assert math.isclose(arm_max, expected_arm, rel_tol=1e-12)
    assert arm_max < 21.0  # 0.6 x requested 35, capped by the canvas edge
    assert arm_max > max_angle


def test_walk_clamp_angles_keep_float_precision() -> None:
    """The per-frame angles follow the full-precision sine curve instead of
    collapsing to rounded integer steps (the round-5 critic measured only
    ±7/±5/0 — the walk stuttered frame to frame). The demo-geometry clamp
    (~8.13°) yields non-integer intermediate angles (8.13°, 5.75°), the
    passing frame is exactly 0.0 (no float residue), and the arms follow the
    same curve at their 0.6x amplitude.
    """
    doc = _make_doc()
    frames = generate_joint_walk_cycle(doc, {})
    angles = [abs(_rot(f, "leg_left").angle_deg) for f in frames]
    # Peak is the exact unrounded clamp value — non-integer, not round(8.13).
    expected_peak = math.degrees(math.atan((9.0 - 3.0 - 2.0) / (2.0 * 14.0)))
    assert math.isclose(max(angles), expected_peak, rel_tol=1e-12)
    assert not float(max(angles)).is_integer()
    # The intermediate frame (phase pi/4) is the unrounded cosine sample.
    assert math.isclose(angles[1], expected_peak * math.cos(math.pi / 4), rel_tol=1e-12)
    assert not float(angles[1]).is_integer()
    # The passing frame is exactly 0.0 — no cos(pi/2) residue, no spurious
    # rotate emitted at the neutral pose.
    assert angles[2] == 0.0
    # Arms keep float precision too: the edge-clamped amplitude
    # asin(2/10) = 11.54° -> 8.16° at pi/4, unrounded.
    arm_angles = [abs(_rot(f, "arm_left").angle_deg) for f in frames]
    expected_arm = math.degrees(math.asin((3.0 - 1.0) / 10.0))
    assert math.isclose(arm_angles[1], expected_arm * math.cos(math.pi / 4), rel_tol=1e-12)
    assert not float(arm_angles[1]).is_integer()


# --- the boot's own width is part of the geometry ------------------------------


def test_walk_clamp_wide_boot_clamps_below_old_floor() -> None:
    """4px-wide boots on 3px shafts: the boot-width-aware clamp must go below
    the old 15° floor — which would fuse the boots at max stride — and the
    rendered boots must stay separate in every frame of the cycle."""
    doc = _make_doc(leg_rows=["oLo."] * 11 + ["ooLo"] * 4)
    frames = generate_joint_walk_cycle(doc, {})
    max_angle = max(abs(_rot(f, "leg_left").angle_deg) for f in frames)
    # safe = atan((gap - boot_width - 2*clearance) / (2*length)) =
    # atan((9 - 4 - 2) / 28) = atan(3/28) ≈ 6.1°.
    expected_safe = math.degrees(math.atan((9.0 - 4.0 - 2.0) / (2.0 * 14.0)))
    assert max_angle < 15.0  # the old floor is no longer allowed to win
    assert max_angle <= expected_safe
    assert max_angle >= 2.0
    # Boots separate at max stride: render the leg pair through the real
    # pipeline and check the max contiguous run over the boot rows.
    rendered = _render_leg_pair(doc, frames)
    runs = [_max_bottom_run(c) for c in rendered]
    assert max(runs) <= 5


def test_walk_clamp_two_degree_floor_for_pathological_geometry() -> None:
    """A hip gap narrower than the boots leaves no room for any swing, so the
    clamp falls to the 2° floor instead of freezing the legs at 0° (or
    overriding the geometry with the old 15° floor)."""
    doc = _make_doc(leg_rows=["oLo."] * 11 + ["ooLo"] * 4, hip_gap=3)
    frames = generate_joint_walk_cycle(doc, {})
    max_angle = max(abs(_rot(f, "leg_left").angle_deg) for f in frames)
    assert max_angle == 2.0


# --- canvas-edge arm clamp -----------------------------------------------------
#
# The round-8 critic's biggest gap: at the full 0.6x counter-swing the demo
# hands reached x=0/x=30 (the canvas edges) and read as flailing rather than
# a natural gait. The arm clamp is a pure function of the doc — how far the
# hand's outermost column rests from the canvas edge, and the arm length
# below the shoulder pivot — capped at asin((edge_clearance - 1px) / length),
# floored at 2°, and skipped by the max_swing override.


def test_walk_clamp_arm_edge_close_clamps_below_requested() -> None:
    """Hands resting 2px from the canvas edge cannot take the full 0.6x
    counter-swing: at 21° the hand tip sweeps 10*sin(21°) ~= 3.6px and would
    cross the edge. The edge clamp caps the swing at asin((2-1)/10) = 5.74°,
    strictly below 0.6x of the requested 35°."""
    doc = _make_doc(
        anchors={
            "hip_l": [12, 34],
            "hip_r": [21, 34],
            "shoulder_l": [3, 15],
            "shoulder_r": [27, 15],
        }
    )
    frames = generate_joint_walk_cycle(doc, {})
    arm_max = max(abs(_rot(f, "arm_left").angle_deg) for f in frames)
    expected = math.degrees(math.asin((2.0 - 1.0) / 10.0))
    assert math.isclose(arm_max, expected, rel_tol=1e-12)
    assert arm_max < 0.6 * 35.0


def test_walk_clamp_arm_two_degree_floor() -> None:
    """A hand already resting on the canvas edge has no room to swing
    (asin(0) = 0°), so the arm clamp falls to the 2° floor instead of
    freezing the arms — same floor semantics as the legs."""
    doc = _make_doc(
        anchors={
            "hip_l": [12, 34],
            "hip_r": [21, 34],
            "shoulder_l": [2, 15],  # hand pixels 1..3 -> 1px from the left edge
            "shoulder_r": [29, 15],  # hand pixels 28..30 -> on the right edge
        }
    )
    frames = generate_joint_walk_cycle(doc, {})
    assert max(abs(_rot(f, "arm_left").angle_deg) for f in frames) == 2.0


def test_walk_clamp_arm_unmeasurable_falls_back() -> None:
    """Both arms present but their shoulder anchors are missing from the doc:
    the edge clearance cannot be measured, so the arms keep the requested
    swing — no crash, no clamp (same fallback the leg clamp uses)."""
    doc = _make_doc(anchors={"hip_l": [12, 34], "hip_r": [21, 34]})
    frames = generate_joint_walk_cycle(doc, {})
    assert max(abs(_rot(f, "arm_left").angle_deg) for f in frames) == 21.0


def test_walk_clamp_arms_stay_inside_canvas_demo_geometry() -> None:
    """The round-8 critic's exact defect on the coherence-demo geometry:
    shoulders at x=5/x=26 on the 31px canvas, 14px arms hanging straight
    down — the left hand 3px from the left edge, the right hand 2px from
    the right edge. The edge clamp (min clearance across the pair = 2px ->
    asin(1/14) = 4.10°) must keep the hands inside the canvas in every
    south walk frame."""
    arms = {
        "arm_left": {
            "anchor": "shoulder_l",
            "layer": 8,
            "shapes": [_bitmap((-2, 0), {"o": "ink", "A": "sleeve_l"}, ["oAo"] * 15)],
        },
        "arm_right": {
            "anchor": "shoulder_r",
            "layer": 8,
            "shapes": [_bitmap((0, 0), {"o": "ink", "B": "sleeve_r"}, ["oBo"] * 15)],
        },
    }
    doc = _make_doc(arms=arms)
    frames = generate_joint_walk_cycle(doc, {})
    arm_max = max(abs(_rot(f, "arm_left").angle_deg) for f in frames)
    expected = math.degrees(math.asin((2.0 - 1.0) / 14.0))
    assert math.isclose(arm_max, expected, rel_tol=1e-12)
    assert arm_max < 0.6 * 35.0
    width = doc.asset.canvas[0]
    rendered = _render_arm_pair(doc, frames)
    for frame in rendered:
        lo, hi = _opaque_x_range(frame)
        assert lo >= 1, (lo, hi)
        assert hi <= width - 1, (lo, hi)


# --- the clamp stays silent ----------------------------------------------------


def test_walk_clamp_stays_silent_on_thick_legs() -> None:
    """Same long, gap-narrow geometry but >= 8px-wide legs: thick limbs read
    as solid masses and keep the full requested swing (no clamp). The
    exemption threshold was raised 5 -> 8 (round-8 gauntlet): the chibi's 5px
    legs (3px fill) MUST still be clamped — their boots can fuse at a 9px hip
    gap — while 8px+ masses can't cross readably."""
    doc = _make_doc(leg_rows=["oooLoooo"] * 15)
    frames = generate_joint_walk_cycle(doc, {})
    assert max(abs(_rot(f, "leg_left").angle_deg) for f in frames) == 35.0


def test_walk_clamp_stays_silent_on_short_limbs() -> None:
    """Short legs (4px below the pivot) on a wide hip gap: the foot tips never
    reach the midline, so no crossing is geometrically possible and the full
    requested swing is kept."""
    doc = _make_doc(leg_rows=["oLo"] * 5, hip_gap=10)
    frames = generate_joint_walk_cycle(doc, {})
    assert max(abs(_rot(f, "leg_left").angle_deg) for f in frames) == 35.0


# --- unmeasurable pairs fall back (never crash) ---------------------------------


def test_walk_clamp_missing_role_falls_back() -> None:
    """Only one leg region discovered: there is no scissor pair to measure.
    The walk must not crash; the arms keep their requested-derived swing,
    capped only by their own canvas-edge geometry (11.54°, not collapsed to
    a leg-derived ~4°)."""
    single_leg = _leg_regions(["oLo"] * 15)
    doc = _make_doc(legs={"leg_left": single_leg["leg_left"]})
    frames = generate_joint_walk_cycle(doc, {})
    assert all("leg_right" not in f.transforms for f in frames)
    expected_arm = math.degrees(math.asin((3.0 - 1.0) / 10.0))
    assert math.isclose(
        max(abs(_rot(f, "arm_left").angle_deg) for f in frames), expected_arm, rel_tol=1e-12
    )


def test_walk_clamp_missing_hip_anchors_falls_back() -> None:
    """Both legs present but their hip anchors are missing from the doc: the
    gap cannot be measured, so the requested swing is used unchanged — no
    crash, no clamp."""
    doc = _make_doc(anchors={"shoulder_l": [5, 15], "shoulder_r": [26, 15]})
    frames = generate_joint_walk_cycle(doc, {})
    assert max(abs(_rot(f, "leg_left").angle_deg) for f in frames) == 35.0


# --- cycle contract preserved ---------------------------------------------------


def test_walk_clamp_phase_periodic_and_deterministic() -> None:
    """The clamp is a constant per cycle, so the phase-periodic contract
    holds: the generator is 2pi-periodic in phase, so the pose after the last
    frame (phase 2pi) equals the pose at frame 0 (phase 0) — verified
    structurally by a 2N-frame cycle reproducing the N-frame cycle exactly at
    even indices (identical phases). Repeated generation returns identical
    frames."""
    doc = _make_doc()
    eight = generate_joint_walk_cycle(doc, {})
    sixteen = generate_joint_walk_cycle(doc, {"frames": 16})
    for i in range(8):
        assert sixteen[2 * i].transforms == eight[i].transforms
    again = generate_joint_walk_cycle(doc, {})
    assert [f.transforms for f in again] == [f.transforms for f in eight]


def test_walk_clamp_max_swing_override() -> None:
    """The optional `max_swing` param replaces the auto-clamp: a cap above the
    requested swing restores the full requested swing on the demo geometry, a
    tight cap forces an even smaller peak, and a negative cap is rejected.
    The override skips the ARM edge clamp too — 0.6x of the capped swing,
    even though the hands rest 3px from the canvas edge."""
    doc = _make_doc()
    full = generate_joint_walk_cycle(doc, {"max_swing": 90})
    assert max(abs(_rot(f, "leg_left").angle_deg) for f in full) == 35.0
    assert max(abs(_rot(f, "arm_left").angle_deg) for f in full) == 21.0  # 0.6 x 35, unclamped
    tight = generate_joint_walk_cycle(doc, {"max_swing": 10})
    assert max(abs(_rot(f, "leg_left").angle_deg) for f in tight) == 10.0
    assert max(abs(_rot(f, "arm_left").angle_deg) for f in tight) == 6.0  # 0.6 x 10, unclamped
    with pytest.raises(ForgeError):
        generate_joint_walk_cycle(doc, {"max_swing": -1})
