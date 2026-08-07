"""`RotateSpec` transform plumbing: schema, merge, lerp, mirror negation, render.

Covers everything between the YAML-facing schema and the composed pixels:
- `schemas.common.RotateSpec` / `RegionTransform.rotate` parsing and defaults;
- `animation.resolver.merge_transforms`: angles add, higher pivot wins;
- `animation.timeline.lerp_transform`: linear angle lerp, pivot snap at t == 1;
- mirrored directions negate inherited override angles and pivot x (alongside the
  existing offset-x negation) for mirror-unsafe regions;
- end-to-end: an `arm_left` region rotated about its shoulder anchor renders swung
  through the real backend path (`render_asset_frames` -> `LocalRenderBackend`).
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from pixel_forge.animation import lerp_transform, merge_transforms, resolve_frames
from pixel_forge.rendering.local import render_asset_frames
from pixel_forge.schemas import RegionTransform, parse_asset_doc
from pixel_forge.schemas.common import RotateSpec

RED = (255, 0, 0, 255)


# ---- schema ----------------------------------------------------------------


def test_rotate_spec_defaults() -> None:
    spec = RotateSpec()
    assert spec.angle_deg == 0.0
    assert spec.pivot is None


def test_rotate_spec_parses_pivot_from_sequence() -> None:
    spec = RotateSpec.model_validate({"angle_deg": 45, "pivot": [2, -3]})
    assert spec.angle_deg == 45.0
    assert spec.pivot == (2, -3)


def test_rotate_spec_forbids_extra_keys() -> None:
    with pytest.raises(ValidationError):
        RotateSpec.model_validate({"angle_deg": 10, "bogus": 1})


def test_region_transform_rotate_defaults_to_none() -> None:
    # Backwards compatibility: existing specs/constructors never see a rotation.
    assert RegionTransform().rotate is None
    assert RegionTransform(offset=(1, 2)).rotate is None


def test_region_transform_parses_rotate_from_yaml_shape() -> None:
    transform = RegionTransform.model_validate(
        {"offset": [1, 0], "rotate": {"angle_deg": -30, "pivot": [0, -4]}}
    )
    assert transform.rotate is not None
    assert transform.rotate.angle_deg == -30.0
    assert transform.rotate.pivot == (0, -4)


# ---- merge_transforms -------------------------------------------------------


def test_merge_rotate_angles_add() -> None:
    merged = merge_transforms(
        RegionTransform(rotate=RotateSpec(angle_deg=20.0)),
        RegionTransform(rotate=RotateSpec(angle_deg=25.0)),
    )
    assert merged.rotate is not None
    assert merged.rotate.angle_deg == 45.0


def test_merge_rotate_none_is_identity() -> None:
    assert merge_transforms().rotate is None
    assert merge_transforms(RegionTransform(), RegionTransform()).rotate is None
    only_base = merge_transforms(
        RegionTransform(rotate=RotateSpec(angle_deg=15.0, pivot=(1, 1))),
        RegionTransform(),
    )
    assert only_base.rotate == RotateSpec(angle_deg=15.0, pivot=(1, 1))


def test_merge_rotate_higher_pivot_wins_else_base_kept() -> None:
    override_wins = merge_transforms(
        RegionTransform(rotate=RotateSpec(angle_deg=10.0, pivot=(1, 0))),
        RegionTransform(rotate=RotateSpec(angle_deg=5.0, pivot=(2, 0))),
    )
    assert override_wins.rotate is not None
    assert override_wins.rotate.pivot == (2, 0)
    base_kept = merge_transforms(
        RegionTransform(rotate=RotateSpec(angle_deg=10.0, pivot=(1, 0))),
        RegionTransform(rotate=RotateSpec(angle_deg=5.0)),
    )
    assert base_kept.rotate is not None
    assert base_kept.rotate.pivot == (1, 0)
    assert base_kept.rotate.angle_deg == 15.0


# ---- lerp_transform ---------------------------------------------------------


def test_lerp_rotate_angle_linear() -> None:
    a = RegionTransform(rotate=RotateSpec(angle_deg=0.0))
    b = RegionTransform(rotate=RotateSpec(angle_deg=90.0))
    mid = lerp_transform(a, b, 0.5)
    assert mid.rotate is not None
    assert mid.rotate.angle_deg == pytest.approx(45.0)
    assert lerp_transform(a, b, 0.0).rotate is not None
    assert lerp_transform(a, b, 0.0).rotate.angle_deg == 0.0  # type: ignore[union-attr]
    assert lerp_transform(a, b, 1.0).rotate.angle_deg == 90.0  # type: ignore[union-attr]


def test_lerp_rotate_missing_side_is_identity_angle() -> None:
    b = RegionTransform(rotate=RotateSpec(angle_deg=90.0, pivot=(3, 3)))
    mid = lerp_transform(RegionTransform(), b, 0.5)
    assert mid.rotate is not None
    assert mid.rotate.angle_deg == pytest.approx(45.0)


def test_lerp_rotate_both_none_stays_none() -> None:
    assert lerp_transform(RegionTransform(), RegionTransform(), 0.5).rotate is None


def test_lerp_rotate_pivot_snaps_like_visible() -> None:
    a = RegionTransform(rotate=RotateSpec(angle_deg=0.0, pivot=(1, 1)))
    b = RegionTransform(rotate=RotateSpec(angle_deg=90.0, pivot=(2, 2)))
    assert lerp_transform(a, b, 0.999).rotate.pivot == (1, 1)  # type: ignore[union-attr]
    assert lerp_transform(a, b, 1.0).rotate.pivot == (2, 2)  # type: ignore[union-attr]


# ---- mirror negation ---------------------------------------------------------


def _mirrored_doc(direction_overrides: dict[str, Any]) -> Any:
    return parse_asset_doc(
        {
            "schema_version": 1,
            "asset": {"id": "engineer", "type": "character", "canvas": [32, 32]},
            "palette": {"id": "p", "colors": [{"id": "skin", "hex": "#e8b58c"}]},
            "directions": ["east", "west"],
            "mirror": {"west": "east"},
            "anchors": {"root": [16, 16]},
            "regions": {
                "insignia": {
                    "anchor": "root",
                    "layer": 0,
                    "mirror_safe": False,
                    "shapes": [{"op": "pixel", "color": "skin", "at": [0, 0]}],
                }
            },
            "direction_overrides": direction_overrides,
            "animations": {
                "idle": {
                    "loop": True,
                    "frames": [{"duration_ms": 100, "events": [], "transforms": {}}],
                }
            },
            "export": {},
            "validation": {},
        }
    )


def test_mirror_inherited_override_negates_angle_pivot_x_and_offset_x() -> None:
    doc = _mirrored_doc(
        {
            "east": {
                "insignia": {
                    "offset": [3, 1],
                    "rotate": {"angle_deg": 30, "pivot": [2, -1]},
                }
            }
        }
    )
    west = next(f for f in resolve_frames(doc) if f.direction == "west")
    assert west.mirrored_from == "east"
    # The safe map keeps the inherited override exactly as authored ...
    safe = west.transforms["insignia"]
    assert safe.offset == (3, 1)
    assert safe.rotate is not None
    assert safe.rotate.angle_deg == 30.0
    assert safe.rotate.pivot == (2, -1)
    # ... the mirror-unsafe map negates x-offset, angle, and pivot x together.
    unsafe = west.mirror_unsafe_transforms["insignia"]
    assert unsafe.offset == (-3, 1)
    assert unsafe.rotate is not None
    assert unsafe.rotate.angle_deg == -30.0
    assert unsafe.rotate.pivot == (-2, -1)


def test_mirror_authored_override_for_mirrored_direction_is_used_verbatim() -> None:
    doc = _mirrored_doc({"west": {"insignia": {"rotate": {"angle_deg": 30, "pivot": [2, 0]}}}})
    west = next(f for f in resolve_frames(doc) if f.direction == "west")
    unsafe = west.mirror_unsafe_transforms["insignia"]
    assert unsafe.rotate is not None
    assert unsafe.rotate.angle_deg == 30.0  # authored for west: not negated
    assert unsafe.rotate.pivot == (2, 0)


# ---- end-to-end render through the real backend ------------------------------


def _character_doc(swing_angle: float) -> Any:
    """Front-view character: torso + a 2x6 arm hanging from a shoulder anchor."""
    return parse_asset_doc(
        {
            "schema_version": 1,
            "asset": {"id": "dummy", "type": "character", "canvas": [32, 32]},
            "palette": {
                "id": "p",
                "colors": [
                    {"id": "shirt", "hex": "#3a6ea5"},
                    {"id": "skin", "hex": "#e8b58c"},
                ],
            },
            "directions": ["south"],
            "anchors": {"root": [16, 20], "shoulder_left": [12, 12]},
            "regions": {
                "torso": {
                    "anchor": "root",
                    "layer": 0,
                    "shapes": [{"op": "rect", "color": "shirt", "at": [-4, -8], "size": [8, 8]}],
                },
                "arm_left": {
                    "anchor": "shoulder_left",
                    "layer": 1,
                    "shapes": [{"op": "rect", "color": "skin", "at": [-1, 0], "size": [2, 6]}],
                },
            },
            "animations": {
                "attack": {
                    "loop": False,
                    "frames": [
                        {"duration_ms": 100, "events": [], "transforms": {}},
                        {
                            "duration_ms": 100,
                            "events": ["swing"],
                            "transforms": {"arm_left": {"rotate": {"angle_deg": swing_angle}}},
                        },
                    ],
                }
            },
            "export": {},
            "validation": {},
        }
    )


def test_arm_rotates_about_shoulder_anchor_end_to_end() -> None:
    frames = render_asset_frames(_character_doc(90.0))
    rest = frames[("attack", "south", 0)]
    swung = frames[("attack", "south", 1)]

    shoulder = (12, 12)
    # Rest pose: the arm hangs straight down from the shoulder.
    assert rest.get_pixel(shoulder[0], shoulder[1] + 4) == (0xE8, 0xB5, 0x8C, 255)
    # Swung +90 deg clockwise: down rotates to the character's left (-x), so the
    # forearm pixel that was 4px below the shoulder is now 4px left of it ...
    assert swung.get_pixel(shoulder[0] - 4, shoulder[1]) == (0xE8, 0xB5, 0x8C, 255)
    # ... and the hanging position is vacated (torso is 4 wide here? no: x=12 is
    # left of the torso span 12..19 at y=16, but y=16 x=12 *is* torso) — check a
    # point the torso does not cover instead.
    assert swung.get_pixel(shoulder[0], shoulder[1] + 4) != (0xE8, 0xB5, 0x8C, 255)
    # The shoulder (pivot) pixel itself stays occupied by the arm at both poses.
    assert rest.get_pixel(*shoulder) == swung.get_pixel(*shoulder)


def test_arm_rotation_sign_swings_opposite_ways() -> None:
    right = render_asset_frames(_character_doc(45.0))[("attack", "south", 1)]
    left = render_asset_frames(_character_doc(-45.0))[("attack", "south", 1)]
    rest = render_asset_frames(_character_doc(0.0))[("attack", "south", 1)]
    assert not right.equals(left)
    assert not right.equals(rest)
    # +45 (clockwise) moves the arm tip towards -x; -45 towards +x.
    skin = (0xE8, 0xB5, 0x8C, 255)
    assert left.get_pixel(15, 15) == skin or left.get_pixel(16, 15) == skin
    assert right.get_pixel(9, 15) == skin or right.get_pixel(8, 15) == skin


def test_zero_angle_rotate_transform_matches_no_rotate_byte_exact() -> None:
    with_zero = render_asset_frames(_character_doc(0.0))
    frames = render_asset_frames(_character_doc(0.0))
    assert with_zero[("attack", "south", 1)].equals(frames[("attack", "south", 1)])
    # And an authored rotate of 0 renders identically to frame 0 (no transform).
    assert with_zero[("attack", "south", 1)].equals(with_zero[("attack", "south", 0)])


def test_rotate_render_is_deterministic() -> None:
    first = render_asset_frames(_character_doc(45.0))
    second = render_asset_frames(_character_doc(45.0))
    for key in first:
        assert first[key].equals(second[key])
