"""Spec -> ResolvedFrame expansion: directions x animations x frames.

Everything the renderer and every validator needs about "what to draw for frame N
of animation A in direction D" is computed here, once, deterministically.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from pixel_forge.errors import ForgeError
from pixel_forge.schemas import (
    AnimationSpec,
    FrameSpec,
    RegionTransform,
    SpriteAssetBase,
    TerrainAsset,
)


@dataclass(frozen=True)
class ResolvedFrame:
    direction: str
    animation: str
    index: int
    duration_ms: int
    events: tuple[str, ...]
    transforms: Mapping[str, RegionTransform]  # region name -> merged transform
    mirrored_from: str | None  # source direction when this one is mirrored


@dataclass(frozen=True)
class ResolvedTileFrame:
    tile_id: str
    animated_tile: str | None
    index: int
    duration_ms: int


def merge_transforms(*layers: RegionTransform) -> RegionTransform:
    """Merge transforms lowest to highest precedence, per-field (not whole-object).

    `offset` and `scale_size` add component-wise; `visible` takes the highest layer
    that set it (non-None); `color_swap` dicts merge with the higher layer winning
    per key. Zero layers returns the identity transform.
    """
    offset = (0, 0)
    scale_size = (0, 0)
    visible: bool | None = None
    color_swap: dict[str, str] = {}
    for layer in layers:
        offset = (offset[0] + layer.offset[0], offset[1] + layer.offset[1])
        scale_size = (scale_size[0] + layer.scale_size[0], scale_size[1] + layer.scale_size[1])
        if layer.visible is not None:
            visible = layer.visible
        color_swap.update(layer.color_swap)
    return RegionTransform(
        offset=offset, visible=visible, color_swap=color_swap, scale_size=scale_size
    )


def _validate_mirror_map(doc: SpriteAssetBase) -> None:
    directions = set(doc.directions)
    for dst, src in doc.mirror.items():
        if dst not in directions:
            raise ForgeError(f"mirror target direction {dst!r} is not in doc.directions")
        if src == dst:
            raise ForgeError(f"mirror direction {dst!r} cannot map to itself")
        if src not in directions:
            raise ForgeError(
                f"mirror source direction {src!r} for {dst!r} is not in doc.directions"
            )
        if src in doc.mirror:
            raise ForgeError(
                f"mirror chain too long: {dst!r} -> {src!r} -> {doc.mirror[src]!r} "
                "(a mirror source cannot itself be a mirrored direction)"
            )


def _direction_overrides(
    doc: SpriteAssetBase, direction: str, mirror_src: str | None
) -> Mapping[str, RegionTransform]:
    if mirror_src is not None:
        if direction in doc.direction_overrides:
            overrides = doc.direction_overrides[direction]
        else:
            overrides = doc.direction_overrides.get(mirror_src, {})
    else:
        overrides = doc.direction_overrides.get(direction, {})
    for region_name in overrides:
        if region_name not in doc.regions:
            raise ForgeError(f"direction override references unknown region {region_name!r}")
    return overrides


def _merge_frame_transforms(
    doc: SpriteAssetBase,
    overrides: Mapping[str, RegionTransform],
    frame: FrameSpec,
) -> Mapping[str, RegionTransform]:
    for region_name in frame.transforms:
        if region_name not in doc.regions:
            raise ForgeError(f"frame transform references unknown region {region_name!r}")
    result: dict[str, RegionTransform] = {}
    for region_name in doc.regions:
        layers = [RegionTransform()]
        if region_name in overrides:
            layers.append(overrides[region_name])
        if region_name in frame.transforms:
            layers.append(frame.transforms[region_name])
        result[region_name] = merge_transforms(*layers)
    return MappingProxyType(result)


def _resolve_direction_frames(
    doc: SpriteAssetBase,
    animation_name: str,
    animation: AnimationSpec,
    direction: str,
) -> list[ResolvedFrame]:
    mirror_src = doc.mirror.get(direction)
    overrides = _direction_overrides(doc, direction, mirror_src)
    return [
        ResolvedFrame(
            direction=direction,
            animation=animation_name,
            index=index,
            duration_ms=frame.duration_ms,
            events=tuple(frame.events),
            transforms=_merge_frame_transforms(doc, overrides, frame),
            mirrored_from=mirror_src,
        )
        for index, frame in enumerate(animation.frames)
    ]


def resolve_frames(doc: SpriteAssetBase) -> list[ResolvedFrame]:
    """Expand a sprite asset doc into a flat, deterministically ordered frame list.

    Order: animations in `doc.animations` declaration order, then directions in
    `doc.directions` order, then frame index ascending.
    """
    if not doc.animations:
        raise ForgeError("doc.animations must not be empty")
    _validate_mirror_map(doc)

    frames: list[ResolvedFrame] = []
    for animation_name, animation in doc.animations.items():
        for direction in doc.directions:
            frames.extend(_resolve_direction_frames(doc, animation_name, animation, direction))
    return frames


def frames_for(doc: SpriteAssetBase, animation: str, direction: str) -> list[ResolvedFrame]:
    return [
        frame
        for frame in resolve_frames(doc)
        if frame.animation == animation and frame.direction == direction
    ]


def animation_duration_ms(doc: SpriteAssetBase, animation: str) -> int:
    if animation not in doc.animations:
        raise ForgeError(f"unknown animation {animation!r}")
    return sum(frame.duration_ms for frame in doc.animations[animation].frames)


def resolve_terrain_frames(doc: TerrainAsset) -> list[ResolvedTileFrame]:
    """Static tiles first (sorted by id, one zero-duration frame each), then each
    animated tile's frames in declaration order.
    """
    frames: list[ResolvedTileFrame] = [
        ResolvedTileFrame(tile_id=tile_id, animated_tile=None, index=0, duration_ms=0)
        for tile_id in sorted(doc.tiles)
    ]
    for animated_name, spec in doc.animated_tiles.items():
        for index, tile_id in enumerate(spec.frames):
            if tile_id not in doc.tiles:
                raise ForgeError(
                    f"animated tile {animated_name!r} references unknown tile {tile_id!r}"
                )
            frames.append(
                ResolvedTileFrame(
                    tile_id=tile_id,
                    animated_tile=animated_name,
                    index=index,
                    duration_ms=spec.frame_duration_ms,
                )
            )
    return frames
