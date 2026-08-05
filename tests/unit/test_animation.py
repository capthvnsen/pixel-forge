from __future__ import annotations

from typing import Any

import pytest

from pixel_forge.animation import (
    Keyframe,
    animation_duration_ms,
    bake_timeline,
    frames_for,
    lerp_transform,
    merge_transforms,
    resolve_frames,
    resolve_terrain_frames,
    sample_timeline,
)
from pixel_forge.errors import ForgeError
from pixel_forge.schemas import RegionTransform, parse_asset_doc


def _doc(
    *,
    directions: list[str] | None = None,
    mirror: dict[str, str] | None = None,
    regions: dict[str, Any] | None = None,
    direction_overrides: dict[str, Any] | None = None,
    animations: dict[str, Any] | None = None,
) -> Any:
    if directions is None:
        directions = ["south", "north"]
    if regions is None:
        regions = {"body": {"anchor": "root", "layer": 0, "shapes": []}}
    if animations is None:
        animations = {
            "idle": {
                "loop": True,
                "frames": [{"duration_ms": 100, "events": [], "transforms": {}}],
            }
        }
    return parse_asset_doc(
        {
            "schema_version": 1,
            "asset": {"id": "engineer", "type": "character", "canvas": [64, 64]},
            "palette": {"id": "p", "colors": [{"id": "skin", "hex": "#e8b58c"}]},
            "directions": directions,
            "mirror": mirror or {},
            "anchors": {"root": [0, 0]},
            "regions": regions,
            "direction_overrides": direction_overrides or {},
            "animations": animations,
            "export": {},
            "validation": {},
        }
    )


def _terrain_doc(
    *,
    tiles: dict[str, Any] | None = None,
    animated_tiles: dict[str, Any] | None = None,
) -> Any:
    if tiles is None:
        tiles = {
            "grass_flat": {"size": [16, 16], "regions": {}, "anchors": {}},
            "dirt_flat": {"size": [16, 16], "regions": {}, "anchors": {}},
        }
    return parse_asset_doc(
        {
            "schema_version": 1,
            "asset": {"id": "forest", "type": "terrain", "canvas": [16, 16]},
            "palette": {"id": "p", "colors": [{"id": "grass", "hex": "#3a9b3a"}]},
            "export": {},
            "validation": {},
            "tiles": tiles,
            "animated_tiles": animated_tiles or {},
        }
    )


# ---- resolve_frames: ordering ----------------------------------------------


def test_frame_count_and_order() -> None:
    doc = _doc(
        directions=["south", "north"],
        animations={
            "idle": {
                "loop": True,
                "frames": [
                    {"duration_ms": 100, "events": [], "transforms": {}},
                    {"duration_ms": 150, "events": ["step"], "transforms": {}},
                ],
            },
            "walk": {
                "loop": True,
                "frames": [{"duration_ms": 80, "events": [], "transforms": {}}],
            },
        },
    )
    frames = resolve_frames(doc)
    assert len(frames) == 2 * 2 + 1 * 2  # idle: 2 frames x 2 dirs, walk: 1 frame x 2 dirs

    order = [(f.animation, f.direction, f.index) for f in frames]
    assert order == [
        ("idle", "south", 0),
        ("idle", "south", 1),
        ("idle", "north", 0),
        ("idle", "north", 1),
        ("walk", "south", 0),
        ("walk", "north", 0),
    ]


def test_empty_animations_raises() -> None:
    doc = _doc(animations={})
    with pytest.raises(ForgeError):
        resolve_frames(doc)


# ---- mirroring ---------------------------------------------------------------


def test_mirrored_direction_matches_source() -> None:
    doc = _doc(
        directions=["south", "east", "west"],
        mirror={"west": "east"},
        animations={
            "idle": {
                "loop": True,
                "frames": [
                    {"duration_ms": 100, "events": [], "transforms": {}},
                    {"duration_ms": 120, "events": ["swing"], "transforms": {}},
                ],
            }
        },
    )
    east_frames = frames_for(doc, "idle", "east")
    west_frames = frames_for(doc, "idle", "west")

    assert len(east_frames) == len(west_frames) == 2
    assert [f.duration_ms for f in east_frames] == [f.duration_ms for f in west_frames]
    assert [f.events for f in east_frames] == [f.events for f in west_frames]
    assert all(f.mirrored_from is None for f in east_frames)
    assert all(f.mirrored_from == "east" for f in west_frames)


def test_self_mirror_raises() -> None:
    doc = _doc(directions=["south", "east"], mirror={"east": "east"})
    with pytest.raises(ForgeError, match="cannot map to itself"):
        resolve_frames(doc)


def test_unknown_mirror_source_raises() -> None:
    doc = _doc(directions=["south", "west"], mirror={"west": "east"})
    with pytest.raises(ForgeError, match="mirror source direction"):
        resolve_frames(doc)


def test_two_hop_mirror_chain_raises() -> None:
    doc = _doc(directions=["north", "west", "east"], mirror={"east": "west", "west": "north"})
    with pytest.raises(ForgeError, match="mirror chain too long"):
        resolve_frames(doc)


def test_mirror_of_undeclared_direction_raises() -> None:
    doc = _doc(directions=["south", "east"], mirror={"west": "east"})
    with pytest.raises(ForgeError, match="mirror target direction"):
        resolve_frames(doc)


# ---- transform merging ---------------------------------------------------------


def test_frame_transform_beats_override_offsets_add() -> None:
    doc = _doc(
        direction_overrides={"south": {"body": {"offset": [2, 3]}}},
        animations={
            "idle": {
                "loop": True,
                "frames": [
                    {
                        "duration_ms": 100,
                        "events": [],
                        "transforms": {"body": {"offset": [10, 0]}},
                    }
                ],
            }
        },
    )
    frame = frames_for(doc, "idle", "south")[0]
    assert frame.transforms["body"].offset == (12, 3)


def test_color_swap_merges_and_visible_takes_highest_set_layer() -> None:
    doc = _doc(
        direction_overrides={
            "south": {"body": {"color_swap": {"skin": "outline"}, "visible": False}}
        },
        animations={
            "idle": {
                "loop": True,
                "frames": [
                    {
                        "duration_ms": 100,
                        "events": [],
                        "transforms": {"body": {"color_swap": {"outline": "skin"}}},
                    }
                ],
            }
        },
    )
    frame = frames_for(doc, "idle", "south")[0]
    merged = frame.transforms["body"]
    assert merged.color_swap == {"skin": "outline", "outline": "skin"}
    assert merged.visible is False  # only the direction override set it


def test_frame_visible_wins_over_direction_override() -> None:
    doc = _doc(
        direction_overrides={"south": {"body": {"visible": False}}},
        animations={
            "idle": {
                "loop": True,
                "frames": [
                    {"duration_ms": 100, "events": [], "transforms": {"body": {"visible": True}}}
                ],
            }
        },
    )
    frame = frames_for(doc, "idle", "south")[0]
    assert frame.transforms["body"].visible is True


def test_unknown_region_in_frame_transform_raises() -> None:
    doc = _doc(
        animations={
            "idle": {
                "loop": True,
                "frames": [
                    {"duration_ms": 100, "events": [], "transforms": {"ghost": {"offset": [1, 1]}}}
                ],
            }
        }
    )
    with pytest.raises(ForgeError):
        resolve_frames(doc)


def test_merge_transforms_zero_layers_is_identity() -> None:
    assert merge_transforms() == RegionTransform()


# ---- frames_for / animation_duration_ms ----------------------------------------


def test_animation_duration_ms_sums_frame_durations() -> None:
    doc = _doc(
        animations={
            "idle": {
                "loop": True,
                "frames": [
                    {"duration_ms": 100, "events": [], "transforms": {}},
                    {"duration_ms": 150, "events": [], "transforms": {}},
                ],
            }
        }
    )
    assert animation_duration_ms(doc, "idle") == 250


def test_animation_duration_ms_unknown_animation_raises() -> None:
    doc = _doc()
    with pytest.raises(ForgeError):
        animation_duration_ms(doc, "nope")


# ---- timeline.lerp_transform ----------------------------------------------------


def test_lerp_transform_offset_rounding_half_away_from_zero() -> None:
    a = RegionTransform(offset=(0, 0))
    b = RegionTransform(offset=(5, -5))

    assert lerp_transform(a, b, 0.0).offset == (0, 0)
    assert lerp_transform(a, b, 0.5).offset == (3, -3)  # 2.5 -> 3, -2.5 -> -3
    assert lerp_transform(a, b, 1.0).offset == (5, -5)


def test_lerp_transform_visible_and_color_swap_snap() -> None:
    a = RegionTransform(visible=True, color_swap={"x": "y"})
    b = RegionTransform(visible=False, color_swap={"z": "w"})

    mid = lerp_transform(a, b, 0.5)
    assert mid.visible is True
    assert mid.color_swap == {"x": "y"}

    end = lerp_transform(a, b, 1.0)
    assert end.visible is False
    assert end.color_swap == {"z": "w"}


# ---- timeline.sample_timeline / bake_timeline -----------------------------------


def test_sample_timeline_clamps_ends() -> None:
    keyframes = [
        Keyframe(0, RegionTransform(offset=(0, 0))),
        Keyframe(100, RegionTransform(offset=(10, 0))),
    ]
    assert sample_timeline(keyframes, -50).offset == (0, 0)
    assert sample_timeline(keyframes, 200).offset == (10, 0)
    assert sample_timeline(keyframes, 50).offset == (5, 0)


def test_sample_timeline_raises_on_empty() -> None:
    with pytest.raises(ForgeError):
        sample_timeline([], 0)


def test_sample_timeline_raises_on_unsorted() -> None:
    keyframes = [
        Keyframe(100, RegionTransform(offset=(10, 0))),
        Keyframe(0, RegionTransform(offset=(0, 0))),
    ]
    with pytest.raises(ForgeError):
        sample_timeline(keyframes, 50)


def test_bake_timeline_samples_at_cumulative_start_times() -> None:
    keyframes = [
        Keyframe(0, RegionTransform(offset=(0, 0))),
        Keyframe(100, RegionTransform(offset=(10, 0))),
    ]
    baked = bake_timeline(keyframes, [50, 50, 50])
    assert len(baked) == 3
    assert [t.offset for t in baked] == [(0, 0), (5, 0), (10, 0)]


# ---- resolve_terrain_frames -----------------------------------------------------


def test_resolve_terrain_frames_ordering() -> None:
    doc = _terrain_doc(
        animated_tiles={"water": {"frames": ["dirt_flat", "grass_flat"], "frame_duration_ms": 150}}
    )
    frames = resolve_terrain_frames(doc)

    static = [f for f in frames if f.animated_tile is None]
    assert [f.tile_id for f in static] == ["dirt_flat", "grass_flat"]
    assert all(f.duration_ms == 0 for f in static)

    animated = [f for f in frames if f.animated_tile is not None]
    assert [(f.tile_id, f.index, f.duration_ms) for f in animated] == [
        ("dirt_flat", 0, 150),
        ("grass_flat", 1, 150),
    ]
    assert all(f.animated_tile == "water" for f in animated)


def test_resolve_terrain_frames_unknown_tile_raises() -> None:
    doc = _terrain_doc(
        animated_tiles={"water": {"frames": ["missing_tile"], "frame_duration_ms": 100}}
    )
    with pytest.raises(ForgeError):
        resolve_terrain_frames(doc)
