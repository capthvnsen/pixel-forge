"""Revision operations: registry, handlers, and inverses (Task 10).

Every operation is applied by dumping the target doc to a plain JSON-safe dict
(`model_dump(mode="json")`), mutating that dict with plain Python, and
re-validating it back into the concrete `AssetDocUnion` subtype. This keeps the
input doc untouched (it is never mutated in place) and gets pydantic's own
validators (palette regex, `duration_ms > 0`, ...) for free on the way out.

Operations that are not exactly invertible via their own forward formula (for
example `resize_region`'s floor-division centring, which is lossy for odd
deltas) stash an exact snapshot of the prior values under an internal
`restore` key in the params they hand back as the inverse. That key is not
part of the public parameter contract described in the plan; callers should
only ever author the public params (`region`/`delta`/... etc). It exists so
`apply(op)` followed by `apply(inverse)` always round-trips exactly, which is
the core guarantee this module has to uphold.

Beyond the low-level Task 10 operations this module also ships the W3-B
semantic editing vocabulary: `swap_palette`, `apply_material`,
`add_component`/`replace_component` (backed by the starter component library
in `pixel_forge.components`), `change_pose`, `repair_outline` — each an
ordinary revision operation with the same apply/inverse/protection contract —
plus `generate_variants`, which is deliberately NOT a revision operation: it
returns N fresh variant docs and never mutates a document or the log, so it
has no apply/inverse pair and no registry entry.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from pydantic import ValidationError

from pixel_forge.animation.resolver import merge_transforms
from pixel_forge.components import ComponentSpec, load_component
from pixel_forge.domain.hashing import content_hash
from pixel_forge.domain.palette import (
    hex_to_rgba,
    hsl_to_rgb,
    relative_luminance,
    resolve_palette,
    rgb_to_hsl,
)
from pixel_forge.errors import OperationError, PaletteError
from pixel_forge.references.curated import CURATED_PALETTES, load_curated_palette
from pixel_forge.schemas.animation import AnimationSpec, FrameSpec
from pixel_forge.schemas.asset import AssetDocUnion, SpriteAssetBase, TerrainAsset, parse_asset_doc
from pixel_forge.schemas.common import Region, RegionTransform
from pixel_forge.schemas.palette import Palette, PaletteColor
from pixel_forge.schemas.revision import JSONValue, OperationSpec


@dataclass(frozen=True)
class OperationInfo:
    name: str
    description: str
    params: tuple[str, ...]


_OPERATION_INFO: tuple[OperationInfo, ...] = (
    OperationInfo(
        "resize_region",
        "Grow or shrink every rect/ellipse shape in a region, centred about its middle.",
        ("region", "delta", "shape_indices"),
    ),
    OperationInfo(
        "translate_region",
        "Shift every shape in a region by a fixed pixel offset.",
        ("region", "offset"),
    ),
    OperationInfo(
        "recolor_region",
        "Remap the palette color ids a region's shapes reference.",
        ("region", "mapping"),
    ),
    OperationInfo(
        "set_frame_duration",
        "Set the duration of one frame, or every frame, of an animation.",
        ("animation", "frame", "duration_ms"),
    ),
    OperationInfo(
        "add_frame",
        "Insert a frame into an animation at a given index.",
        ("animation", "at", "frame"),
    ),
    OperationInfo(
        "remove_frame",
        "Remove a frame from an animation at a given index.",
        ("animation", "at"),
    ),
    OperationInfo(
        "set_region_visibility",
        "Show or hide a region for specific animation frames or directions.",
        ("region", "visible", "animation", "frames", "directions"),
    ),
    OperationInfo(
        "replace_spec",
        "Replace the asset's entire spec document; for structural edits the other operations "
        "don't cover.",
        ("spec",),
    ),
    OperationInfo(
        "swap_palette",
        "Replace the doc's palette with a curated palette (or the doc's own palette id, a "
        "no-op), optionally remapping every shape/transform colour reference to the new "
        "palette's ids by closest hue.",
        ("palette_id", "remap"),
    ),
    OperationInfo(
        "apply_material",
        "Remap a region's (or every region's) shape colours toward a material profile, "
        "quantized onto the doc's existing palette.",
        ("material", "region"),
    ),
    OperationInfo(
        "add_component",
        "Insert a starter component's regions anchored at an existing or newly created "
        "anchor, extending the palette with any missing component colours from curated "
        "palettes.",
        ("component", "anchor", "anchor_at", "offset"),
    ),
    OperationInfo(
        "replace_component",
        "Remove existing region(s), then insert a starter component's regions in their "
        "place (re-equip).",
        ("component", "anchor", "replace", "anchor_at", "offset"),
    ),
    OperationInfo(
        "change_pose",
        "Set an animation's per-frame transforms to a named pose template (idle, "
        "attack_anticipation, attack_strike) derived from the doc's region-name "
        "conventions.",
        ("animation", "pose", "frames"),
    ),
    OperationInfo(
        "repair_outline",
        "Close gaps in authored bitmap outlines by filling any non-outline cell "
        "(transparent or fill-coloured) flanked by at least two outline-coloured "
        "pixels with the palette's outline colour.",
        ("region",),
    ),
)


def available_operations() -> list[OperationInfo]:
    """All operations this module knows how to apply, for CLI/MCP advertising."""
    return list(_OPERATION_INFO)


# --- param extraction helpers -----------------------------------------------


def _require_str(params: dict[str, JSONValue], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str):
        raise OperationError(f"operation param {key!r} must be a string, got {value!r}")
    return value


def _require_int(params: dict[str, JSONValue], key: str) -> int:
    value = params.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise OperationError(f"operation param {key!r} must be an int, got {value!r}")
    return value


def _require_bool(params: dict[str, JSONValue], key: str) -> bool:
    value = params.get(key)
    if not isinstance(value, bool):
        raise OperationError(f"operation param {key!r} must be a bool, got {value!r}")
    return value


def _require_vec2(params: dict[str, JSONValue], key: str) -> tuple[int, int]:
    value = params.get(key)
    if (
        not isinstance(value, list)
        or len(value) != 2
        or not all(isinstance(v, int) and not isinstance(v, bool) for v in value)
    ):
        raise OperationError(f"operation param {key!r} must be a 2-int [x, y] list, got {value!r}")
    return (cast(int, value[0]), cast(int, value[1]))


def _as_dict(value: JSONValue | None, name: str) -> dict[str, JSONValue]:
    if not isinstance(value, dict):
        raise OperationError(f"operation param {name!r} must be an object")
    return value


def _as_str_str_dict(value: JSONValue | None, name: str) -> dict[str, str]:
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise OperationError(f"operation param {name!r} must be a mapping of string to string")
    return cast(dict[str, str], value)


# --- doc/region/animation lookups -------------------------------------------


def _require_sprite_doc(doc: AssetDocUnion) -> SpriteAssetBase:
    if not isinstance(doc, SpriteAssetBase):
        raise OperationError(
            f"asset {doc.asset.id!r} is a {doc.kind!r} asset; region/animation revision "
            "operations only apply to character, enemy, and prop assets"
        )
    return doc


def _require_region(sprite: SpriteAssetBase, name: str) -> Region:
    region = sprite.regions.get(name)
    if region is None:
        raise OperationError(
            f"unknown region {name!r}; available regions: {sorted(sprite.regions)}"
        )
    return region


def _require_unprotected_region(sprite: SpriteAssetBase, name: str) -> Region:
    region = _require_region(sprite, name)
    if region.protected:
        raise OperationError(f"region {name!r} is protected and cannot be modified")
    return region


def _require_animation(sprite: SpriteAssetBase, name: str) -> AnimationSpec:
    animation = sprite.animations.get(name)
    if animation is None:
        raise OperationError(
            f"unknown animation {name!r}; available animations: {sorted(sprite.animations)}"
        )
    return animation


def _require_frame_index(animation: AnimationSpec, idx: int) -> None:
    if idx < 0 or idx >= len(animation.frames):
        raise OperationError(
            f"unknown frame index {idx}; valid indices: 0..{len(animation.frames) - 1}"
        )


def _require_direction(sprite: SpriteAssetBase, name: str) -> None:
    if name not in sprite.directions:
        raise OperationError(
            f"unknown direction {name!r}; available directions: {sprite.directions}"
        )


def _resolve_shape_indices(region: Region, raw: JSONValue | None) -> list[int]:
    if raw is None:
        return list(range(len(region.shapes)))
    if not isinstance(raw, list):
        raise OperationError("operation param 'shape_indices' must be a list of ints")
    indices: list[int] = []
    for item in raw:
        if not isinstance(item, int) or isinstance(item, bool):
            raise OperationError(f"'shape_indices' must contain only ints, got {item!r}")
        if item < 0 or item >= len(region.shapes):
            raise OperationError(
                f"shape index {item} out of range for region with {len(region.shapes)} shapes"
            )
        indices.append(item)
    return indices


# --- protection enforcement --------------------------------------------------


def check_protection(before: AssetDocUnion, after: AssetDocUnion, protect: Sequence[str]) -> None:
    """Verify none of `protect`'s anchors/regions changed between `before` and `after`.

    Every name in `protect` must be either an anchor or a region on `before`;
    an unrecognised name raises just like an unrecognised region/animation does
    elsewhere in this module.
    """
    if not protect:
        return
    before_anchors = getattr(before, "anchors", {})
    before_regions: dict[str, Region] = getattr(before, "regions", {})
    after_anchors = getattr(after, "anchors", {})
    after_regions: dict[str, Region] = getattr(after, "regions", {})
    for name in protect:
        if name in before_anchors:
            if before_anchors[name] != after_anchors.get(name):
                raise OperationError(f"protected anchor {name!r} moved")
        elif name in before_regions:
            before_shapes = [s.model_dump(mode="json") for s in before_regions[name].shapes]
            after_region = after_regions.get(name)
            after_shapes = (
                [s.model_dump(mode="json") for s in after_region.shapes]
                if after_region is not None
                else None
            )
            if after_shapes != before_shapes:
                raise OperationError(f"protected region {name!r} shapes changed")
        else:
            raise OperationError(
                f"op.protect entry {name!r} is not a known anchor or region "
                f"(anchors: {sorted(before_anchors)}, regions: {sorted(before_regions)})"
            )


# --- operation handlers -------------------------------------------------------
# Each handler reads the typed, unmutated `doc` for validation/lookups and
# writes its changes into `data` (a fresh `doc.model_dump(mode="json")`). It
# returns (inverse_operation_name, inverse_operation_params).

Handler = Callable[[AssetDocUnion, dict[str, Any], OperationSpec], tuple[str, dict[str, JSONValue]]]


def _resize_region(
    doc: AssetDocUnion, data: dict[str, Any], op: OperationSpec
) -> tuple[str, dict[str, JSONValue]]:
    sprite = _require_sprite_doc(doc)
    region_name = _require_str(op.params, "region")
    region = _require_unprotected_region(sprite, region_name)
    shapes_data = data["regions"][region_name]["shapes"]
    indices = _resolve_shape_indices(region, op.params.get("shape_indices"))
    indices_json: JSONValue = list(indices)

    if indices and all(shapes_data[idx]["op"] == "bitmap" for idx in indices):
        raise OperationError(
            f"resize_region: region {region_name!r} contains only bitmap shapes; resize does "
            "not apply to bitmap art (there is no `size` to grow or shrink, and resampling the "
            "pixels would blur them). Re-author the art at the new size and use import_region "
            "instead."
        )

    restore = op.params.get("restore")
    if restore is not None:
        restore_map = _as_dict(restore, "restore")
        prior: dict[str, JSONValue] = {}
        for idx in indices:
            shape_data = shapes_data[idx]
            if shape_data["op"] not in ("rect", "ellipse"):
                continue
            key = str(idx)
            if key not in restore_map:
                continue
            vals = restore_map[key]
            if not isinstance(vals, list) or len(vals) != 4:
                raise OperationError(f"'restore' entry {key!r} must be a 4-int list")
            prior[key] = [
                shape_data["at"][0],
                shape_data["at"][1],
                shape_data["size"][0],
                shape_data["size"][1],
            ]
            shape_data["at"] = [vals[0], vals[1]]
            shape_data["size"] = [vals[2], vals[3]]
        return "resize_region", {
            "region": region_name,
            "shape_indices": indices_json,
            "restore": prior,
        }

    delta = _require_vec2(op.params, "delta")
    prior = {}
    for idx in indices:
        shape_data = shapes_data[idx]
        if shape_data["op"] not in ("rect", "ellipse"):
            continue
        old_at = shape_data["at"]
        old_size = shape_data["size"]
        new_size = [old_size[0] + delta[0], old_size[1] + delta[1]]
        if new_size[0] < 1 or new_size[1] < 1:
            raise OperationError(
                f"resize_region: shape {idx} in region {region_name!r} would shrink below "
                f"1px (resulting size {tuple(new_size)})"
            )
        new_at = [old_at[0] - delta[0] // 2, old_at[1] - delta[1] // 2]
        prior[str(idx)] = [old_at[0], old_at[1], old_size[0], old_size[1]]
        shape_data["at"] = new_at
        shape_data["size"] = new_size
    return "resize_region", {"region": region_name, "shape_indices": indices_json, "restore": prior}


def _translate_region(
    doc: AssetDocUnion, data: dict[str, Any], op: OperationSpec
) -> tuple[str, dict[str, JSONValue]]:
    sprite = _require_sprite_doc(doc)
    region_name = _require_str(op.params, "region")
    _require_unprotected_region(sprite, region_name)
    offset = _require_vec2(op.params, "offset")
    shapes_data = data["regions"][region_name]["shapes"]
    for shape_data in shapes_data:
        if shape_data["op"] == "line":
            shape_data["start"] = [
                shape_data["start"][0] + offset[0],
                shape_data["start"][1] + offset[1],
            ]
            shape_data["end"] = [
                shape_data["end"][0] + offset[0],
                shape_data["end"][1] + offset[1],
            ]
        else:
            shape_data["at"] = [shape_data["at"][0] + offset[0], shape_data["at"][1] + offset[1]]
    return "translate_region", {"region": region_name, "offset": [-offset[0], -offset[1]]}


def _recolor_region(
    doc: AssetDocUnion, data: dict[str, Any], op: OperationSpec
) -> tuple[str, dict[str, JSONValue]]:
    sprite = _require_sprite_doc(doc)
    region_name = _require_str(op.params, "region")
    _require_unprotected_region(sprite, region_name)
    mapping = _as_str_str_dict(op.params.get("mapping"), "mapping")
    palette_ids = {color.id for color in doc.palette.colors}
    for target in mapping.values():
        if target not in palette_ids:
            raise OperationError(
                f"recolor_region: target palette id {target!r} is not in palette "
                f"{doc.palette.id!r} (known ids: {sorted(palette_ids)})"
            )
    if len(set(mapping.values())) != len(mapping):
        raise OperationError(
            f"recolor_region: mapping {mapping} is not injective (two source colors map to "
            "the same target), so it cannot be reversed"
        )
    shapes_data = data["regions"][region_name]["shapes"]
    for shape_data in shapes_data:
        if shape_data["op"] == "bitmap":
            key_data = shape_data["key"]
            for char, color in key_data.items():
                if color in mapping:
                    key_data[char] = mapping[color]
        else:
            color = shape_data["color"]
            if color in mapping:
                shape_data["color"] = mapping[color]
    inverse_mapping: dict[str, JSONValue] = {v: k for k, v in mapping.items()}
    return "recolor_region", {"region": region_name, "mapping": inverse_mapping}


def _set_frame_duration(
    doc: AssetDocUnion, data: dict[str, Any], op: OperationSpec
) -> tuple[str, dict[str, JSONValue]]:
    sprite = _require_sprite_doc(doc)
    animation_name = _require_str(op.params, "animation")
    animation = _require_animation(sprite, animation_name)
    frames_data = data["animations"][animation_name]["frames"]

    restore = op.params.get("restore")
    if restore is not None:
        restore_map = _as_dict(restore, "restore")
        prior: dict[str, JSONValue] = {}
        for key, value in restore_map.items():
            idx = int(key)
            _require_frame_index(animation, idx)
            if not isinstance(value, int) or isinstance(value, bool):
                raise OperationError(f"'restore' entry {key!r} must be an int duration_ms")
            prior[key] = frames_data[idx]["duration_ms"]
            frames_data[idx]["duration_ms"] = value
        return "set_frame_duration", {"animation": animation_name, "restore": prior}

    duration_ms = _require_int(op.params, "duration_ms")
    frame_param = op.params.get("frame")
    if frame_param is None:
        indices: range | list[int] = range(len(animation.frames))
    else:
        if not isinstance(frame_param, int) or isinstance(frame_param, bool):
            raise OperationError(
                f"operation param 'frame' must be an int or null, got {frame_param!r}"
            )
        _require_frame_index(animation, frame_param)
        indices = [frame_param]
    prior = {}
    for idx in indices:
        prior[str(idx)] = frames_data[idx]["duration_ms"]
        frames_data[idx]["duration_ms"] = duration_ms
    return "set_frame_duration", {"animation": animation_name, "restore": prior}


def _add_frame(
    doc: AssetDocUnion, data: dict[str, Any], op: OperationSpec
) -> tuple[str, dict[str, JSONValue]]:
    sprite = _require_sprite_doc(doc)
    animation_name = _require_str(op.params, "animation")
    animation = _require_animation(sprite, animation_name)
    at = _require_int(op.params, "at")
    if at < 0 or at > len(animation.frames):
        raise OperationError(
            f"add_frame: index {at} out of range for animation {animation_name!r} "
            f"(valid: 0..{len(animation.frames)})"
        )
    frame_payload = op.params.get("frame")
    if not isinstance(frame_payload, dict):
        raise OperationError("add_frame: 'frame' param must be an object (a serialised FrameSpec)")
    try:
        validated_frame = FrameSpec.model_validate(frame_payload)
    except ValidationError as exc:
        raise OperationError(f"add_frame: invalid frame spec: {exc}") from exc
    frames_data = data["animations"][animation_name]["frames"]
    frames_data.insert(at, validated_frame.model_dump(mode="json"))
    return "remove_frame", {"animation": animation_name, "at": at}


def _remove_frame(
    doc: AssetDocUnion, data: dict[str, Any], op: OperationSpec
) -> tuple[str, dict[str, JSONValue]]:
    sprite = _require_sprite_doc(doc)
    animation_name = _require_str(op.params, "animation")
    animation = _require_animation(sprite, animation_name)
    at = _require_int(op.params, "at")
    _require_frame_index(animation, at)
    if len(animation.frames) <= 1:
        raise OperationError(
            f"remove_frame: cannot remove the last remaining frame of animation {animation_name!r}"
        )
    frames_data = data["animations"][animation_name]["frames"]
    removed = frames_data.pop(at)
    return "add_frame", {"animation": animation_name, "at": at, "frame": removed}


def _transform_or_none(value: JSONValue | None) -> dict[str, JSONValue] | None:
    if value is None:
        return None
    return _as_dict(value, "transform")


def _set_region_visibility(
    doc: AssetDocUnion, data: dict[str, Any], op: OperationSpec
) -> tuple[str, dict[str, JSONValue]]:
    sprite = _require_sprite_doc(doc)
    region_name = _require_str(op.params, "region")
    _require_unprotected_region(sprite, region_name)

    restore = op.params.get("restore")
    animation_param = op.params.get("animation")

    if restore is not None:
        restore_map = _as_dict(restore, "restore")
        prior: dict[str, JSONValue] = {}
        for key, value in restore_map.items():
            kind, _, ident = key.partition(":")
            if kind == "frame":
                if not isinstance(animation_param, str):
                    raise OperationError(
                        "set_region_visibility restore: 'animation' is required to restore frame "
                        "transforms"
                    )
                idx = int(ident)
                frames_data = data["animations"][animation_param]["frames"][idx]["transforms"]
                prior[key] = _transform_or_none(frames_data.get(region_name))
                new_value = _transform_or_none(value)
                if new_value is None:
                    frames_data.pop(region_name, None)
                else:
                    frames_data[region_name] = new_value
            elif kind == "direction":
                overrides_data = data["direction_overrides"].setdefault(ident, {})
                prior[key] = _transform_or_none(overrides_data.get(region_name))
                new_value = _transform_or_none(value)
                if new_value is None:
                    overrides_data.pop(region_name, None)
                    if not overrides_data:
                        data["direction_overrides"].pop(ident, None)
                else:
                    overrides_data[region_name] = new_value
            else:
                raise OperationError(f"set_region_visibility restore: unrecognised key {key!r}")
        return "set_region_visibility", {
            "region": region_name,
            "animation": animation_param,
            "restore": prior,
        }

    visible = _require_bool(op.params, "visible")
    frames_targets = op.params.get("frames")
    directions_targets = op.params.get("directions")
    prior = {}

    if isinstance(frames_targets, list) and frames_targets:
        if not isinstance(animation_param, str):
            raise OperationError(
                "set_region_visibility: 'animation' is required when 'frames' is given"
            )
        animation = _require_animation(sprite, animation_param)
        for frame_raw in frames_targets:
            if not isinstance(frame_raw, int) or isinstance(frame_raw, bool):
                raise OperationError(f"'frames' must contain only ints, got {frame_raw!r}")
            _require_frame_index(animation, frame_raw)
            key = f"frame:{frame_raw}"
            frame_data = data["animations"][animation_param]["frames"][frame_raw]
            existing_raw = frame_data["transforms"].get(region_name)
            prior[key] = _transform_or_none(existing_raw)
            existing = (
                RegionTransform.model_validate(existing_raw) if existing_raw else RegionTransform()
            )
            merged = merge_transforms(existing, RegionTransform(visible=visible))
            frame_data["transforms"][region_name] = merged.model_dump(mode="json")
    elif isinstance(directions_targets, list) and directions_targets:
        for direction_raw in directions_targets:
            if not isinstance(direction_raw, str):
                raise OperationError(
                    f"'directions' must contain only strings, got {direction_raw!r}"
                )
            _require_direction(sprite, direction_raw)
            key = f"direction:{direction_raw}"
            overrides_data = data["direction_overrides"].setdefault(direction_raw, {})
            existing_raw = overrides_data.get(region_name)
            prior[key] = _transform_or_none(existing_raw)
            existing = (
                RegionTransform.model_validate(existing_raw) if existing_raw else RegionTransform()
            )
            merged = merge_transforms(existing, RegionTransform(visible=visible))
            overrides_data[region_name] = merged.model_dump(mode="json")
    else:
        raise OperationError(
            "set_region_visibility requires either a non-empty 'frames' list (with 'animation') "
            "or a non-empty 'directions' list"
        )

    return "set_region_visibility", {
        "region": region_name,
        "animation": animation_param,
        "restore": prior,
    }


def _check_region_dict(before: dict[str, Region], after: dict[str, Region], *, scope: str) -> None:
    for name, region in before.items():
        if not region.protected:
            continue
        after_region = after.get(name)
        if after_region is None or after_region.model_dump(mode="json") != region.model_dump(
            mode="json"
        ):
            raise OperationError(f"replace_spec: protected region {scope}{name!r} changed")


def _check_protected_regions(before: AssetDocUnion, after: AssetDocUnion) -> None:
    """Refuse a `replace_spec` that touches any `protected: true` region.

    Mirrors the outright block every other operation applies via
    `_require_unprotected_region`: a protected region must come through the
    replacement byte-for-byte, not just with unchanged shapes.
    """
    if isinstance(before, SpriteAssetBase) and isinstance(after, SpriteAssetBase):
        _check_region_dict(before.regions, after.regions, scope="")
    elif isinstance(before, TerrainAsset) and isinstance(after, TerrainAsset):
        for tile_id, tile in before.tiles.items():
            after_tile = after.tiles.get(tile_id)
            after_regions = after_tile.regions if after_tile is not None else {}
            _check_region_dict(tile.regions, after_regions, scope=f"tile {tile_id!r} ")


def _replace_spec(
    doc: AssetDocUnion, data: dict[str, Any], op: OperationSpec
) -> tuple[str, dict[str, JSONValue]]:
    spec_param = _as_dict(op.params.get("spec"), "spec")
    try:
        new_doc = parse_asset_doc(dict(spec_param))
    except ValidationError as exc:
        raise OperationError(
            f"replace_spec: replacement spec failed schema validation: {exc}"
        ) from exc
    if new_doc.asset.id != doc.asset.id:
        raise OperationError(
            f"replace_spec cannot change asset.id from {doc.asset.id!r} to {new_doc.asset.id!r}"
        )
    if type(new_doc) is not type(doc):
        raise OperationError(
            f"replace_spec cannot change asset kind from {doc.kind!r} to {new_doc.kind!r}"
        )
    _check_protected_regions(doc, new_doc)
    data.clear()
    data.update(new_doc.model_dump(mode="json"))
    return "replace_spec", {"spec": doc.model_dump(mode="json")}


# --- semantic editing operations (W3-B) -------------------------------------
#
# The operations below give an agent a safe, semantic editing vocabulary on top
# of the low-level ops: swap_palette, apply_material, add_component /
# replace_component, change_pose, repair_outline. Each follows the module's
# contract exactly: it reads the typed `doc` for validation, writes into a
# fresh `data` dump, returns (inverse_name, inverse_params), and round-trips
# byte-exactly because the inverse either carries a `restore` snapshot of the
# exact prior values (the module's established pattern) or re-derives the
# forward deterministically from the restored document.
#
# `generate_variants` is NOT a revision operation: it returns N fresh variant
# docs and never mutates a document or touches the log, so it has no apply /
# inverse pair and is deliberately absent from `_OPERATION_INFO`/`_HANDLERS`.

# --- shared helpers -----------------------------------------------------------


def _optional_vec2(params: dict[str, JSONValue], key: str) -> tuple[int, int] | None:
    value = params.get(key)
    if value is None:
        return None
    return _require_vec2(params, key)


def _shift_shape(shape_data: dict[str, Any], dx: int, dy: int) -> None:
    """Translate every coordinate in `shape_data` by (dx, dy), in place."""
    if dx == 0 and dy == 0:
        return
    op = shape_data.get("op")
    if op == "line":
        shape_data["start"] = [shape_data["start"][0] + dx, shape_data["start"][1] + dy]
        shape_data["end"] = [shape_data["end"][0] + dx, shape_data["end"][1] + dy]
    elif op in ("polygon", "curve"):
        shape_data["points"] = [[p[0] + dx, p[1] + dy] for p in shape_data["points"]]
    elif op == "bezier":
        for key in ("p0", "p1", "p2"):
            shape_data[key] = [shape_data[key][0] + dx, shape_data[key][1] + dy]
    else:
        shape_data["at"] = [shape_data["at"][0] + dx, shape_data["at"][1] + dy]


def _shape_color_ids(shape_data: dict[str, Any]) -> set[str]:
    """Palette color ids one serialised shape references (bitmap keys or `color`)."""
    if shape_data.get("op") == "bitmap":
        key = shape_data.get("key")
        if isinstance(key, dict):
            return {cid for cid in key.values() if isinstance(cid, str)}
        return set()
    color = shape_data.get("color")
    return {color} if isinstance(color, str) else set()


def _remap_shape_colors(shape_data: dict[str, Any], mapping: Mapping[str, str]) -> None:
    """Apply `mapping` (old palette id -> new palette id) to one shape, in place."""
    if not mapping:
        return
    if shape_data.get("op") == "bitmap":
        key = shape_data.get("key")
        if isinstance(key, dict):
            for char, color_id in list(key.items()):
                if isinstance(color_id, str) and color_id in mapping:
                    key[char] = mapping[color_id]
    else:
        color = shape_data.get("color")
        if isinstance(color, str) and color in mapping:
            shape_data["color"] = mapping[color]


def _iter_region_dicts(data: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Every region dict in `data`: sprite `regions`, plus terrain tile regions."""
    regions = data.get("regions")
    if isinstance(regions, dict):
        yield from regions.values()
    tiles = data.get("tiles")
    if isinstance(tiles, dict):
        for tile_data in tiles.values():
            tile_regions = tile_data.get("regions")
            if not isinstance(tile_regions, dict):
                continue
            yield from tile_regions.values()


def _walk_region_shapes(data: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Every shape dict in `data`: sprite `regions`, plus terrain tile regions."""
    for region_data in _iter_region_dicts(data):
        shapes = region_data.get("shapes")
        if isinstance(shapes, list):
            yield from shapes


def _protected_region_names(data: dict[str, Any]) -> set[str]:
    """Top-level region names flagged `protected` in `data`.

    Terrain tiles carry their own per-region `protected` flags, but only sprite
    docs have `color_swap` transforms keyed by region name, so the name set is
    only consulted there.
    """
    regions = data.get("regions")
    if not isinstance(regions, dict):
        return set()
    return {
        name
        for name, region_data in regions.items()
        if isinstance(region_data, dict) and region_data.get("protected")
    }


def _walk_color_swap_maps_named(data: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """Every `color_swap` map in `data`, paired with the region it applies to."""
    overrides = data.get("direction_overrides")
    if isinstance(overrides, dict):
        for region_map in overrides.values():
            if not isinstance(region_map, dict):
                continue
            for region_name, transform in region_map.items():
                if isinstance(transform, dict) and isinstance(transform.get("color_swap"), dict):
                    yield region_name, transform["color_swap"]
    animations = data.get("animations")
    if isinstance(animations, dict):
        for animation_data in animations.values():
            frames = animation_data.get("frames")
            if not isinstance(frames, list):
                continue
            for frame_data in frames:
                transforms = frame_data.get("transforms")
                if not isinstance(transforms, dict):
                    continue
                for region_name, transform in transforms.items():
                    if isinstance(transform, dict) and isinstance(
                        transform.get("color_swap"), dict
                    ):
                        yield region_name, transform["color_swap"]


def _doc_color_refs(doc: AssetDocUnion) -> set[str]:
    """Every palette color id the doc references: shapes + `color_swap` maps."""
    data = doc.model_dump(mode="json")
    refs: set[str] = set()
    for shape_data in _walk_region_shapes(data):
        refs |= _shape_color_ids(shape_data)
    for _region_name, swap_map in _walk_color_swap_maps_named(data):
        refs.update(k for k in swap_map if isinstance(k, str))
        refs.update(v for v in swap_map.values() if isinstance(v, str))
    return refs


def _split_color_refs_by_protection(data: dict[str, Any]) -> tuple[set[str], set[str]]:
    """`(refs used by unprotected regions, refs used by protected regions)`.

    The unprotected set drives swap_palette's remap (only regions the operation
    is allowed to touch consume new-palette slots); the protected set is what
    the operation must carry over unchanged. `color_swap` maps are attributed
    to their owning region name.
    """
    unprotected: set[str] = set()
    protected: set[str] = set()
    for region_data in _iter_region_dicts(data):
        target = protected if region_data.get("protected") else unprotected
        shapes = region_data.get("shapes")
        if isinstance(shapes, list):
            for shape_data in shapes:
                target.update(_shape_color_ids(shape_data))
    protected_names = _protected_region_names(data)
    for region_name, swap_map in _walk_color_swap_maps_named(data):
        target = protected if region_name in protected_names else unprotected
        # Both KEYS and VALUES must be attributed: a colour_swap key that no
        # shape references is schema-legal and render-visible, and if it is
        # not remapped (or carried when protected) it dangles after the swap.
        target.update(k for k in swap_map if isinstance(k, str))
        target.update(v for v in swap_map.values() if isinstance(v, str))
    return unprotected, protected


def _remap_all_color_refs(
    data: dict[str, Any], mapping: Mapping[str, str], *, skip_protected: bool = False
) -> None:
    """Apply `mapping` to every shape colour and `color_swap` id in `data`.

    With `skip_protected`, regions flagged `protected` (sprite regions and
    terrain tile regions) and transforms owned by protected region names are
    left untouched — swap_palette's protection contract.
    """
    if not mapping:
        return
    protected_names = _protected_region_names(data) if skip_protected else set()
    for region_data in _iter_region_dicts(data):
        if skip_protected and region_data.get("protected"):
            continue
        shapes = region_data.get("shapes")
        if isinstance(shapes, list):
            for shape_data in shapes:
                _remap_shape_colors(shape_data, mapping)
    for region_name, swap_map in _walk_color_swap_maps_named(data):
        if region_name in protected_names:
            continue
        for key, value in list(swap_map.items()):
            new_key = mapping.get(key, key)
            new_value = mapping.get(value, value)
            del swap_map[key]
            swap_map[new_key] = new_value


def _hue_aware_nearest(hex_str: str, palette: Palette, exclude: set[str]) -> str | None:
    """Nearest palette id to `hex_str`, weighting hue distance over value/saturation.

    Achromatic colours (saturation < 0.08 either side) get no hue penalty, so a
    grey matches on lightness/saturation — an outline grey lands on the
    palette's darkest tone rather than on a bright hue. Ties keep the earlier
    declared id. Deterministic.
    """
    hue0, sat0, light0 = rgb_to_hsl(hex_to_rgba(hex_str)[:3])
    best_id: str | None = None
    best_dist = float("inf")
    for color in palette.colors:
        if color.id in exclude:
            continue
        hue1, sat1, light1 = rgb_to_hsl(hex_to_rgba(color.hex)[:3])
        if sat0 >= 0.08 and sat1 >= 0.08:
            dh = abs(hue0 - hue1) % 360.0
            dh = min(dh, 360.0 - dh) / 180.0
        else:
            dh = 0.0
        dist = 4.0 * dh * dh + (sat0 - sat1) ** 2 + (light0 - light1) ** 2
        if dist < best_dist:
            best_dist = dist
            best_id = color.id
    return best_id


def _build_remap(
    doc: AssetDocUnion, new_palette: Palette, refs: set[str] | None = None
) -> dict[str, str]:
    """Injective old-id -> new-id remap of every colour `refs` references.

    `refs` defaults to every colour the doc references; swap_palette passes the
    unprotected-region refs so protected colours never consume new-palette
    slots (they are carried over instead). Ids the new palette already declares
    map to themselves (and keep their slot); every other id maps to its
    hue-aware nearest unused id, resolved greedily in sorted id order so the
    result is deterministic and reversible.
    """
    if refs is None:
        refs = _doc_color_refs(doc)
    new_ids = set(new_palette.by_id)
    mapping: dict[str, str] = {}
    taken: set[str] = set()
    for old in sorted(refs):
        old_color = doc.palette.by_id.get(old)
        if old_color is None:
            raise OperationError(
                f"swap_palette: the doc references colour {old!r}, which is not in its own "
                "palette"
            )
        if old in new_ids:
            taken.add(old)
            continue
        target = _hue_aware_nearest(old_color.hex, new_palette, taken)
        if target is None:
            raise OperationError(
                f"swap_palette: every colour of palette {new_palette.id!r} is already "
                f"assigned; cannot remap {old!r}"
            )
        mapping[old] = target
        taken.add(target)
    return mapping


def _outline_color_id(doc: AssetDocUnion) -> str:
    """The palette id repair_outline paints with: the `outline` role colour if
    declared, else the darkest declared colour by relative luminance."""
    for color in doc.palette.colors:
        if color.role == "outline":
            return color.id
    darkest = min(doc.palette.colors, key=lambda c: relative_luminance(hex_to_rgba(c.hex)))
    return darkest.id


def _nearest_unused(
    rgba: tuple[int, int, int, int], palette: Palette, taken: set[str]
) -> str | None:
    """Nearest palette id to `rgba` by squared RGB distance, skipping `taken`.

    Ties keep the earlier declared colour (stable sort). None when every
    palette colour is taken.
    """
    def dist(color: PaletteColor) -> int:
        cr, cg, cb, _ = hex_to_rgba(color.hex)
        return (cr - rgba[0]) ** 2 + (cg - rgba[1]) ** 2 + (cb - rgba[2]) ** 2

    for color in sorted(palette.colors, key=dist):
        if color.id not in taken:
            return color.id
    return None


def _curated_color_source(color_id: str) -> PaletteColor | None:
    """A validated `PaletteColor` with id `color_id` from any curated palette.

    Curated palettes are searched in sorted name order and declaration order
    within each, so the lookup is deterministic.
    """
    for name in sorted(CURATED_PALETTES):
        for color_data in CURATED_PALETTES[name]["colors"]:
            if color_data.get("id") == color_id:
                return PaletteColor.model_validate(color_data)
    return None


def _component_color_additions(doc: AssetDocUnion, component: ComponentSpec) -> list[PaletteColor]:
    """Palette colours a component needs that the doc doesn't declare, sourced
    from curated palettes so inserted shapes always reference approved ids."""
    known = set(doc.palette.by_id)
    additions: list[PaletteColor] = []
    for color_id in sorted(component.color_ids):
        if color_id in known:
            continue
        source = _curated_color_source(color_id)
        if source is None:
            raise OperationError(
                f"component {component.id!r} references colour {color_id!r}, which is "
                "neither in the doc palette nor in any curated palette"
            )
        additions.append(source)
        known.add(color_id)
    return additions


def _check_palette_headroom(doc: AssetDocUnion, additions: int, op_name: str) -> None:
    new_size = len(doc.palette.colors) + additions
    if new_size > doc.validation.palette_limit:
        raise OperationError(
            f"{op_name}: adding {additions} colour(s) would grow the palette to "
            f"{new_size}, over the doc's validation.palette_limit of "
            f"{doc.validation.palette_limit}"
        )


def _strip_region_transforms(
    data: dict[str, Any], region_names: Sequence[str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Remove per-frame and direction-override transforms referencing any of
    `region_names`, returning the removed entries for the inverse to restore.

    The animation resolver raises on a frame transform that references an
    unknown region (animation/resolver.py::_merge_frame_transforms), so
    replace_component must strip these when it deletes a region.
    """
    names = set(region_names)
    frame_transforms: dict[str, Any] = {}
    animations = data.get("animations")
    if isinstance(animations, dict):
        for anim_name, animation_data in animations.items():
            frames_data = animation_data.get("frames")
            if not isinstance(frames_data, list):
                continue
            for idx, frame_data in enumerate(frames_data):
                transforms = frame_data.get("transforms")
                if not isinstance(transforms, dict):
                    continue
                hit = {name: transforms.pop(name) for name in list(transforms) if name in names}
                if hit:
                    frame_transforms.setdefault(anim_name, {})[str(idx)] = hit
    override_transforms: dict[str, Any] = {}
    overrides = data.get("direction_overrides")
    if isinstance(overrides, dict):
        for direction, region_map in overrides.items():
            if not isinstance(region_map, dict):
                continue
            hit = {name: region_map.pop(name) for name in list(region_map) if name in names}
            if hit:
                override_transforms[direction] = hit
    return frame_transforms, override_transforms


# --- materials ----------------------------------------------------------------

MaterialTransform = Callable[[tuple[int, int, int]], tuple[int, int, int]]


def _material_worn_fabric(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    """Desaturate ~40% and add a little contrast: sun-faded cloth."""
    hue, sat, light = rgb_to_hsl(rgb)
    sat = max(0.0, sat * 0.6)
    light = min(1.0, max(0.0, 0.5 + (light - 0.5) * 1.18))
    return hsl_to_rgb(hue, sat, light)


def _material_painted_metal(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    """Deepen saturation (stronger hue) and lift lightness: a lit painted surface."""
    hue, sat, light = rgb_to_hsl(rgb)
    sat = min(1.0, sat * 1.4)
    light = min(1.0, light * 0.9 + 0.06)
    return hsl_to_rgb(hue, sat, light)


def _material_rusty_iron(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    """Desaturate, darken, and nudge hue toward red-brown rust."""
    hue, sat, light = rgb_to_hsl(rgb)
    sat = max(0.0, sat * 0.85)
    light = max(0.0, light * 0.9)
    hue = (hue + 14.0) % 360.0
    return hsl_to_rgb(hue, sat, light)


MATERIAL_PROFILES: dict[str, MaterialTransform] = {
    "worn_fabric": _material_worn_fabric,
    "painted_metal": _material_painted_metal,
    "rusty_iron": _material_rusty_iron,
}


# --- pose templates -----------------------------------------------------------

@dataclass(frozen=True)
class PoseRoles:
    """Regions a pose template can drive, discovered by name convention.

    Mirrors animation/cycles.py's discovery: names containing
    leg/arm/body/head split into left/right by an l/left/r/right token;
    regions named *shadow*, anchored at `feet`, or marked protected are
    static and never moved.
    """

    body: str | None
    head: str | None
    arm_left: str | None
    arm_right: str | None
    leg_left: str | None
    leg_right: str | None
    static: frozenset[str]


def _side(name: str) -> str | None:
    for token in name.split("_"):
        if token in ("l", "left"):
            return "left"
        if token in ("r", "right"):
            return "right"
    return None


def _discover_pose_roles(sprite: SpriteAssetBase) -> PoseRoles:
    body: str | None = None
    head: str | None = None
    leg_left: str | None = None
    leg_right: str | None = None
    arm_left: str | None = None
    arm_right: str | None = None
    static: set[str] = set()
    feet_anchor = "feet" if "feet" in sprite.anchors else None

    for name, region in sprite.regions.items():
        lower = name.lower()
        if (
            region.protected
            or "shadow" in lower
            or (feet_anchor is not None and region.anchor == feet_anchor)
        ):
            static.add(name)
        elif body is None and "body" in lower:
            body = name
        elif head is None and "head" in lower:
            head = name

    for name in sprite.regions:
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

    return PoseRoles(
        body=body,
        head=head,
        leg_left=leg_left,
        leg_right=leg_right,
        arm_left=arm_left,
        arm_right=arm_right,
        static=frozenset(static),
    )


PoseTemplate = Callable[[PoseRoles, int, int], dict[str, RegionTransform]]


def _pose_idle(roles: PoseRoles, frame_idx: int, _frame_count: int) -> dict[str, RegionTransform]:
    """Gentle 2-frame breathing: body and head bob 1px, arms sway counter-phase."""
    bob = -1 if frame_idx % 2 == 0 else 0
    sway = 1 if frame_idx % 2 == 0 else -1
    transforms: dict[str, RegionTransform] = {}
    if roles.body is not None:
        transforms[roles.body] = RegionTransform(offset=(0, bob))
    if roles.head is not None:
        transforms[roles.head] = RegionTransform(offset=(0, bob))
    if roles.arm_left is not None:
        transforms[roles.arm_left] = RegionTransform(offset=(sway, bob))
    if roles.arm_right is not None:
        transforms[roles.arm_right] = RegionTransform(offset=(-sway, bob))
    return transforms


def _pose_attack_anticipation(
    roles: PoseRoles, frame_idx: int, _frame_count: int
) -> dict[str, RegionTransform]:
    """2-frame wind-up: body leans back and crouches, both arms raise high."""
    windup = frame_idx % 2 == 1
    transforms: dict[str, RegionTransform] = {}
    if roles.body is not None:
        transforms[roles.body] = RegionTransform(offset=(-1, -1) if windup else (0, 0))
    if roles.head is not None:
        transforms[roles.head] = RegionTransform(offset=(0, -1) if windup else (0, 0))
    if roles.arm_left is not None:
        transforms[roles.arm_left] = RegionTransform(offset=(0, -2) if windup else (0, -1))
    if roles.arm_right is not None:
        transforms[roles.arm_right] = RegionTransform(offset=(0, -2) if windup else (0, -1))
    return transforms


def _pose_attack_strike(
    roles: PoseRoles, frame_idx: int, _frame_count: int
) -> dict[str, RegionTransform]:
    """2-frame lunge: body drives forward, arms extend; recovery frame returns."""
    lunge = frame_idx % 2 == 0
    transforms: dict[str, RegionTransform] = {}
    if roles.body is not None:
        transforms[roles.body] = RegionTransform(offset=(2, 0) if lunge else (0, 0))
    if roles.head is not None:
        transforms[roles.head] = RegionTransform(offset=(1, 0) if lunge else (0, 0))
    if roles.arm_left is not None:
        transforms[roles.arm_left] = RegionTransform(offset=(2, -1) if lunge else (0, 0))
    if roles.arm_right is not None:
        transforms[roles.arm_right] = RegionTransform(offset=(2, -1) if lunge else (0, 0))
    return transforms


POSE_TEMPLATES: dict[str, PoseTemplate] = {
    "idle": _pose_idle,
    "attack_anticipation": _pose_attack_anticipation,
    "attack_strike": _pose_attack_strike,
}


# --- handlers ----------------------------------------------------------------


def _swap_palette(
    doc: AssetDocUnion, data: dict[str, Any], op: OperationSpec
) -> tuple[str, dict[str, JSONValue]]:
    restore = op.params.get("restore")
    if restore is not None:
        restore_map = _as_dict(restore, "restore")
        old_palette = _as_dict(restore_map.get("palette"), "palette")
        restore_mapping = _as_str_str_dict(restore_map.get("mapping"), "mapping")
        pre_palette = dict(data["palette"])
        data["palette"] = dict(old_palette)
        # Reversing an injective mapping restores every colour reference exactly.
        # Protected regions were never remapped forward, so they are skipped here
        # too (their ids were carried into the swapped palette and are restored
        # wholesale by the palette replacement above).
        _remap_all_color_refs(data, {v: k for k, v in restore_mapping.items()}, skip_protected=True)
        return "swap_palette", {
            "restore": cast(
                dict[str, JSONValue],
                {
                    "palette": pre_palette,
                    "mapping": {v: k for k, v in restore_mapping.items()},
                },
            )
        }

    palette_id = _require_str(op.params, "palette_id")
    remap_raw = op.params.get("remap", True)
    if not isinstance(remap_raw, bool):
        raise OperationError(f"operation param 'remap' must be a bool, got {remap_raw!r}")

    unprotected_refs, protected_refs = _split_color_refs_by_protection(data)
    for cid in sorted(protected_refs):
        if cid not in doc.palette.by_id:
            raise OperationError(
                f"swap_palette: protected region references colour {cid!r}, which is not "
                "in the doc palette"
            )

    if palette_id == doc.palette.id:
        # "A palette by id in the doc": swapping to the doc's own palette is a
        # no-op, but it still records a reversible (identity) revision.
        return "swap_palette", {
            "restore": cast(
                dict[str, JSONValue],
                {"palette": doc.palette.model_dump(mode="json"), "mapping": {}},
            )
        }
    try:
        new_palette = load_curated_palette(palette_id)
    except PaletteError as exc:
        raise OperationError(str(exc)) from exc
    if remap_raw:
        # Protected regions are frozen: their colours are never remapped
        # (and never consume new-palette slots); anything they reference
        # that the new palette lacks is carried over verbatim below.
        mapping = _build_remap(doc, new_palette, refs=unprotected_refs)
    else:
        missing = sorted(unprotected_refs - set(new_palette.by_id))
        if missing:
            raise OperationError(
                f"swap_palette: remap=false but the doc references colours missing from "
                f"palette {palette_id!r}: {missing}"
            )
        mapping = {}
    data["palette"] = new_palette.model_dump(mode="json")

    if mapping:
        _remap_all_color_refs(data, mapping, skip_protected=True)
    carried = sorted(protected_refs - set(new_palette.by_id))
    if carried:
        data["palette"]["colors"].extend(
            doc.palette.by_id[cid].model_dump(mode="json") for cid in carried
        )
    return "swap_palette", {
        "restore": cast(
            dict[str, JSONValue],
            {"palette": doc.palette.model_dump(mode="json"), "mapping": mapping},
        )
    }


def _apply_material(
    doc: AssetDocUnion, data: dict[str, Any], op: OperationSpec
) -> tuple[str, dict[str, JSONValue]]:
    sprite = _require_sprite_doc(doc)
    material = _require_str(op.params, "material")
    transform = MATERIAL_PROFILES.get(material)
    if transform is None:
        raise OperationError(
            f"unknown material {material!r}; available: {sorted(MATERIAL_PROFILES)}"
        )
    region_param = op.params.get("region")
    if region_param is not None and not isinstance(region_param, str):
        raise OperationError("operation param 'region' must be a string or null")

    restore = op.params.get("restore")
    if restore is not None:
        restore_map = _as_dict(restore, "restore")
        for region_name, mapping_raw in restore_map.items():
            if not isinstance(mapping_raw, dict):
                raise OperationError(
                    f"apply_material restore: entry {region_name!r} must be a mapping"
                )
            shapes_data = data.get("regions", {}).get(region_name, {}).get("shapes")
            if not isinstance(shapes_data, list):
                raise OperationError(f"apply_material restore: unknown region {region_name!r}")
            reverse = {v: k for k, v in mapping_raw.items() if isinstance(v, str)}
            for shape_data in shapes_data:
                _remap_shape_colors(shape_data, reverse)
        return "apply_material", {k: v for k, v in op.params.items() if k != "restore"}

    if region_param is None:
        regions = [
            (name, region) for name, region in sprite.regions.items() if not region.protected
        ]
    else:
        regions = [(region_param, _require_unprotected_region(sprite, region_param))]

    if not doc.palette.colors:
        raise OperationError("apply_material: the doc palette is empty; nothing to quantize onto")
    resolved = resolve_palette(doc.palette)
    material_restore: dict[str, JSONValue] = {}
    for region_name, region in regions:
        used = sorted(
            {
                cid
                for shape in region.shapes
                for cid in _shape_color_ids(shape.model_dump(mode="json"))
            }
        )
        if not used:
            continue
        if len(used) > len(doc.palette.colors):
            raise OperationError(
                f"apply_material: region {region_name!r} uses {len(used)} colours but the "
                f"palette has {len(doc.palette.colors)}; cannot build a reversible remap"
            )
        region_mapping: dict[str, str] = {}
        taken: set[str] = set()
        for old in used:
            old_color = doc.palette.by_id.get(old)
            if old_color is None:
                raise OperationError(
                    f"apply_material: region {region_name!r} references colour {old!r} which "
                    "is not in the doc palette"
                )
            rgba = hex_to_rgba(old_color.hex)
            target = (*transform(rgba[:3]), rgba[3])
            best = resolved.nearest(target)
            if best in taken:
                alternative = _nearest_unused(target, doc.palette, taken)
                if alternative is None:
                    raise OperationError(
                        f"apply_material: region {region_name!r} has no unused palette "
                        f"colour left for {old!r}; enlarge the palette or split the region"
                    )
                best = alternative
            if best != old:
                region_mapping[old] = best
            taken.add(best)
        if region_mapping:
            shapes_data = data["regions"][region_name]["shapes"]
            for shape_data in shapes_data:
                _remap_shape_colors(shape_data, region_mapping)
            material_restore[region_name] = {k: v for k, v in region_mapping.items()}
    return "apply_material", {
        "material": material,
        "region": region_param,
        "restore": material_restore,
    }


def _shape_extent(shape_data: dict[str, Any]) -> tuple[int, int, int, int]:
    """Anchor-relative inclusive bbox (x0, y0, x1, y1) of one serialised shape."""
    op = shape_data.get("op")
    if op == "line":
        sx, sy = shape_data["start"]
        ex, ey = shape_data["end"]
        return (min(sx, ex), min(sy, ey), max(sx, ex), max(sy, ey))
    if op in ("polygon", "curve"):
        points = shape_data["points"]
        xs = [int(p[0]) for p in points]
        ys = [int(p[1]) for p in points]
        return (min(xs), min(ys), max(xs), max(ys))
    if op == "bezier":
        xs = [int(shape_data[key][0]) for key in ("p0", "p1", "p2")]
        ys = [int(shape_data[key][1]) for key in ("p0", "p1", "p2")]
        return (min(xs), min(ys), max(xs), max(ys))
    if op == "bitmap":
        rows = shape_data.get("rows")
        if isinstance(rows, list) and rows:
            width = len(rows[0])
            height = len(rows)
        else:
            width = height = 0
        ax, ay = shape_data["at"]
        return (int(ax), int(ay), int(ax) + width - 1, int(ay) + height - 1)
    ax, ay = shape_data["at"]
    size = shape_data.get("size")
    if isinstance(size, list) and len(size) == 2:
        return (int(ax), int(ay), int(ax) + int(size[0]) - 1, int(ay) + int(size[1]) - 1)
    return (int(ax), int(ay), int(ax), int(ay))


def _component_bbox(
    component: ComponentSpec, offset: tuple[int, int]
) -> tuple[int, int, int, int]:
    """Anchor-relative inclusive bbox of the component's shapes, `offset` applied."""
    ox, oy = offset
    extents = [
        _shape_extent(shape.model_dump(mode="json"))
        for region in component.regions.values()
        for shape in region.shapes
    ]
    if not extents:
        return (0, 0, -1, -1)
    return (
        min(x0 + ox for x0, _y0, _x1, _y1 in extents),
        min(y0 + oy for _x0, y0, _x1, _y1 in extents),
        max(x1 + ox for _x0, _y0, x1, _y1 in extents),
        max(y1 + oy for _x0, _y0, _x1, y1 in extents),
    )


def _check_component_placement(
    component: ComponentSpec,
    anchor_name: str,
    anchor_pos: tuple[int, int],
    offset: tuple[int, int],
    canvas_size: tuple[int, int],
) -> None:
    """Placement sanity for add_component: the anchor and the rendered footprint
    must land inside the canvas.

    A component whose bbox falls outside the canvas would render clipped or
    entirely off-screen — the classic misplaced-anchor symptom (a helmet
    attached below the head instead of above it). Raises `OperationError` so
    the caller re-attaches at a sensible anchor or offset.
    """
    ax, ay = anchor_pos
    cw, ch = canvas_size
    if not (0 <= ax < cw and 0 <= ay < ch):
        raise OperationError(
            f"add_component: anchor {anchor_name!r} at {anchor_pos} lands outside the "
            f"canvas {canvas_size}; pick an anchor inside the canvas"
        )
    x0, y0, x1, y1 = _component_bbox(component, offset)
    x0 += ax
    y0 += ay
    x1 += ax
    y1 += ay
    if x0 < 0 or y0 < 0 or x1 >= cw or y1 >= ch:
        raise OperationError(
            f"add_component: component {component.id!r} at anchor {anchor_name!r} "
            f"({anchor_pos}) with offset {offset} renders outside the canvas "
            f"{canvas_size} (bbox x{x0}..{x1}, y{y0}..{y1}); pick a different anchor "
            "or offset"
        )


def _add_component(
    doc: AssetDocUnion, data: dict[str, Any], op: OperationSpec
) -> tuple[str, dict[str, JSONValue]]:
    sprite = _require_sprite_doc(doc)
    component_name = _require_str(op.params, "component")
    anchor_name = _require_str(op.params, "anchor")

    restore = op.params.get("restore")
    if restore is not None:
        restore_map = _as_dict(restore, "restore")
        added_regions = restore_map.get("regions")
        if not isinstance(added_regions, list):
            raise OperationError("add_component restore: 'regions' must be a list of names")
        for name in added_regions:
            if isinstance(name, str):
                data["regions"].pop(name, None)
        added_anchor = restore_map.get("anchor")
        if isinstance(added_anchor, str):
            data["anchors"].pop(added_anchor, None)
        added_colors = restore_map.get("colors")
        if isinstance(added_colors, list):
            removed = {c for c in added_colors if isinstance(c, str)}
            data["palette"]["colors"] = [
                c for c in data["palette"]["colors"] if c["id"] not in removed
            ]
        return "add_component", {k: v for k, v in op.params.items() if k != "restore"}

    component = load_component(component_name)
    anchor_at = _optional_vec2(op.params, "anchor_at")
    created_pos: tuple[int, int] | None = None
    if anchor_at is not None:
        if anchor_name in sprite.anchors:
            raise OperationError(
                f"add_component: anchor {anchor_name!r} already exists; pass 'anchor_at' "
                "only when creating a new anchor"
            )
        created_anchor: str | None = anchor_name
        created_pos = anchor_at
    else:
        if anchor_name not in sprite.anchors:
            raise OperationError(
                f"add_component: unknown anchor {anchor_name!r}; available: "
                f"{sorted(sprite.anchors)} (or pass 'anchor_at' to create it)"
            )
        created_anchor = None
    offset = _optional_vec2(op.params, "offset") or (0, 0)

    collisions = sorted(set(component.regions) & set(sprite.regions))
    if collisions:
        raise OperationError(
            f"add_component: region name(s) already exist: {collisions}; use "
            "replace_component to swap an existing region out first"
        )
    anchor_pos = created_pos if created_pos is not None else sprite.anchors[anchor_name]
    _check_component_placement(
        component, anchor_name, anchor_pos, offset, doc.asset.canvas
    )
    additions = _component_color_additions(doc, component)
    _check_palette_headroom(doc, len(additions), "add_component")

    for region_name, region in component.regions.items():
        region_data = region.model_dump(mode="json")
        region_data["anchor"] = anchor_name
        for shape_data in region_data["shapes"]:
            _shift_shape(shape_data, offset[0], offset[1])
        data["regions"][region_name] = region_data
    if created_anchor is not None and created_pos is not None:
        data["anchors"][anchor_name] = [created_pos[0], created_pos[1]]
    data["palette"]["colors"].extend(color.model_dump(mode="json") for color in additions)

    return "add_component", cast(
        dict[str, JSONValue],
        {
            "component": component_name,
            "anchor": anchor_name,
            "anchor_at": op.params.get("anchor_at"),
            "offset": op.params.get("offset"),
            "restore": {
                "regions": sorted(component.regions),
                "anchor": created_anchor,
                "colors": [color.id for color in additions],
            },
        },
    )


def _replace_component(
    doc: AssetDocUnion, data: dict[str, Any], op: OperationSpec
) -> tuple[str, dict[str, JSONValue]]:
    sprite = _require_sprite_doc(doc)
    component_name = _require_str(op.params, "component")
    anchor_name = _require_str(op.params, "anchor")

    restore = op.params.get("restore")
    if restore is not None:
        restore_map = _as_dict(restore, "restore")
        added_regions = restore_map.get("added_regions")
        if not isinstance(added_regions, list):
            raise OperationError(
                "replace_component restore: 'added_regions' must be a list of names"
            )
        for name in added_regions:
            if isinstance(name, str):
                data["regions"].pop(name, None)
        added_anchor = restore_map.get("added_anchor")
        if isinstance(added_anchor, str):
            data["anchors"].pop(added_anchor, None)
        added_colors = restore_map.get("added_colors")
        if isinstance(added_colors, list):
            removed = {c for c in added_colors if isinstance(c, str)}
            data["palette"]["colors"] = [
                c for c in data["palette"]["colors"] if c["id"] not in removed
            ]
        saved_regions = restore_map.get("removed_regions")
        if isinstance(saved_regions, dict):
            for name, region_data in saved_regions.items():
                data["regions"][name] = region_data
        frame_transforms = restore_map.get("frame_transforms")
        if isinstance(frame_transforms, dict):
            for anim_name, frames_map in frame_transforms.items():
                if not isinstance(frames_map, dict):
                    continue
                animations = data.get("animations")
                frames_data = (
                    animations.get(anim_name, {}).get("frames")
                    if isinstance(animations, dict)
                    else None
                )
                if not isinstance(frames_data, list):
                    continue
                for idx_raw, region_map in frames_map.items():
                    if not isinstance(region_map, dict):
                        continue
                    try:
                        idx = int(idx_raw)
                    except ValueError:
                        continue
                    if 0 <= idx < len(frames_data):
                        frames_data[idx]["transforms"].update(region_map)
        override_transforms = restore_map.get("override_transforms")
        if isinstance(override_transforms, dict):
            for direction, region_map in override_transforms.items():
                if not isinstance(region_map, dict):
                    continue
                overrides = data.setdefault("direction_overrides", {}).setdefault(
                    direction, {}
                )
                overrides.update(region_map)
        return "replace_component", {k: v for k, v in op.params.items() if k != "restore"}

    component = load_component(component_name)
    anchor_at = _optional_vec2(op.params, "anchor_at")
    created_pos: tuple[int, int] | None = None
    if anchor_at is not None:
        if anchor_name in sprite.anchors:
            raise OperationError(
                f"replace_component: anchor {anchor_name!r} already exists; pass "
                "'anchor_at' only when creating a new anchor"
            )
        created_anchor: str | None = anchor_name
        created_pos = anchor_at
    else:
        if anchor_name not in sprite.anchors:
            raise OperationError(
                f"replace_component: unknown anchor {anchor_name!r}; available: "
                f"{sorted(sprite.anchors)} (or pass 'anchor_at' to create it)"
            )
        created_anchor = None
    offset = _optional_vec2(op.params, "offset") or (0, 0)

    replace_raw = op.params.get("replace")
    if replace_raw is None:
        replace_names = [name for name in component.regions if name in sprite.regions]
        if not replace_names:
            raise OperationError(
                f"replace_component: no region to replace — component {component_name!r} "
                f"has no regions already in the doc ({sorted(component.regions)}); pass "
                "'replace' with the region names to swap out, or use add_component"
            )
    else:
        if not isinstance(replace_raw, list) or not all(isinstance(n, str) for n in replace_raw):
            raise OperationError("operation param 'replace' must be a list of region names")
        replace_names = cast(list[str], replace_raw)
    replace_set = set(replace_names)
    for name in replace_names:
        if name not in sprite.regions:
            raise OperationError(
                f"replace_component: unknown region to replace {name!r}; available: "
                f"{sorted(sprite.regions)}"
            )
        _require_unprotected_region(sprite, name)
    collisions = sorted((set(component.regions) - replace_set) & set(sprite.regions))
    if collisions:
        raise OperationError(
            f"replace_component: component regions collide with existing regions outside "
            f"the replaced set: {collisions}"
        )
    additions = _component_color_additions(doc, component)
    _check_palette_headroom(doc, len(additions), "replace_component")

    removed_frame_transforms, removed_override_transforms = _strip_region_transforms(
        data, replace_names
    )
    removed_regions: dict[str, Any] = {}
    for name in replace_names:
        removed_regions[name] = data["regions"].pop(name)
    for region_name, region in component.regions.items():
        region_data = region.model_dump(mode="json")
        region_data["anchor"] = anchor_name
        shapes = region_data.get("shapes")
        if isinstance(shapes, list):
            for shape_data in shapes:
                if isinstance(shape_data, dict):
                    _shift_shape(shape_data, offset[0], offset[1])
        data["regions"][region_name] = region_data
    if created_anchor is not None and created_pos is not None:
        data["anchors"][anchor_name] = [created_pos[0], created_pos[1]]
    data["palette"]["colors"].extend(color.model_dump(mode="json") for color in additions)

    replace_params = cast(
        dict[str, JSONValue],
        {
            "component": component_name,
            "anchor": anchor_name,
            "anchor_at": op.params.get("anchor_at"),
            "offset": op.params.get("offset"),
            "replace": cast(list[str], replace_raw) if replace_raw is not None else replace_names,
            "restore": {
                "removed_regions": removed_regions,
                "added_regions": sorted(component.regions),
                "added_anchor": created_anchor,
                "added_colors": [color.id for color in additions],
                "frame_transforms": removed_frame_transforms,
                "override_transforms": removed_override_transforms,
            },
        },
    )
    return "replace_component", replace_params


def _change_pose(
    doc: AssetDocUnion, data: dict[str, Any], op: OperationSpec
) -> tuple[str, dict[str, JSONValue]]:
    sprite = _require_sprite_doc(doc)
    animation_name = _require_str(op.params, "animation")
    animation = _require_animation(sprite, animation_name)
    pose_name = _require_str(op.params, "pose")
    template = POSE_TEMPLATES.get(pose_name)
    if template is None:
        raise OperationError(f"unknown pose {pose_name!r}; available: {sorted(POSE_TEMPLATES)}")
    frames_data = data["animations"][animation_name]["frames"]

    restore = op.params.get("restore")
    if restore is not None:
        restore_map = _as_dict(restore, "restore")
        for key, value in restore_map.items():
            idx = int(key)
            _require_frame_index(animation, idx)
            if not isinstance(value, dict):
                raise OperationError(
                    f"change_pose restore: entry {key!r} must be a transforms object"
                )
            frames_data[idx]["transforms"] = dict(value)
        return "change_pose", {k: v for k, v in op.params.items() if k != "restore"}

    frame_param = op.params.get("frames")
    if frame_param is None:
        indices = list(range(len(animation.frames)))
    else:
        if not isinstance(frame_param, list) or not all(
            isinstance(f, int) and not isinstance(f, bool) for f in frame_param
        ):
            raise OperationError("operation param 'frames' must be a list of ints or null")
        indices = cast(list[int], list(frame_param))
        for idx in indices:
            _require_frame_index(animation, idx)

    roles = _discover_pose_roles(sprite)
    pose_restore: dict[str, JSONValue] = {}
    for idx in indices:
        frame_data = frames_data[idx]
        prior = frame_data["transforms"]
        merged = dict(prior)
        for region_name, transform in template(roles, idx, len(animation.frames)).items():
            merged[region_name] = transform.model_dump(mode="json")
        if merged != prior:
            pose_restore[str(idx)] = prior
            frame_data["transforms"] = merged
    return "change_pose", {
        "animation": animation_name,
        "pose": pose_name,
        "frames": frame_param,
        "restore": pose_restore,
    }


def _repair_bitmap_rows(
    rows: list[str], outline_chars: set[str], outline_char: str
) -> tuple[list[str], bool]:
    """Fill cells that break an authored outline ring.

    A cell is a gap when it sits ON the ring path:
    - A transparent cell (`.`, space) is a HOLE in the ring: fill it when it
      has at least two outline neighbours (the ring passes around it).
    - A fill-coloured cell (`x`) is a ring-path BREAK: fill it only when it
      has exactly two outline neighbours on OPPOSITE sides (vertical
      above+below, or horizontal left+right) — the ring passes through it.
      A fully-enclosed interior (three or four outline neighbours, like the
      3x3 closed ring's centre) and a concave-corner cell (two outline
      neighbours on the same side) are legitimate art and stay.
    The cells to fill are computed from the original rows before any fill is
    applied (two passes), so a fill never creates new qualifying neighbours and
    the result is deterministic and conservative.
    """
    height = len(rows)
    width = len(rows[0])
    fill_cells: list[tuple[int, int]] = []
    for r in range(height):
        row = rows[r]
        for c in range(width):
            if row[c] in outline_chars:
                continue
            up = r > 0 and rows[r - 1][c] in outline_chars
            down = r < height - 1 and rows[r + 1][c] in outline_chars
            left = c > 0 and rows[r][c - 1] in outline_chars
            right = c < width - 1 and rows[r][c + 1] in outline_chars
            total = up + down + left + right
            if total == 0:
                continue
            opposite = (up and down) or (left and right)
            if row[c] in (".", " "):
                # Transparent hole: fill when the ring surrounds it (>= 3 sides
                # — a genuine enclosed hollow) OR when exactly two outline
                # neighbours lie on OPPOSITE sides (a 1px ring-path gap). A
                # same-side pair is a concave corner of a donut hollow and is
                # legitimate art — leave it (the R3 critic's donut defect).
                if total >= 3 or (total == 2 and opposite):
                    fill_cells.append((r, c))
            else:
                # Fill-coloured ring-path break: exactly two outline neighbours
                # on opposite sides. (A fully-enclosed interior — three or four
                # outline neighbours — and a concave-corner same-side pair are
                # legitimate art and stay.)
                if total == 2 and opposite:
                    fill_cells.append((r, c))
    if not fill_cells:
        return rows, False
    repaired = list(rows)
    for r, c in fill_cells:
        repaired[r] = repaired[r][:c] + outline_char + repaired[r][c + 1 :]
    return repaired, True


def _repair_outline(
    doc: AssetDocUnion, data: dict[str, Any], op: OperationSpec
) -> tuple[str, dict[str, JSONValue]]:
    sprite = _require_sprite_doc(doc)
    region_param = op.params.get("region")
    if region_param is not None and not isinstance(region_param, str):
        raise OperationError("operation param 'region' must be a string or null")
    if region_param is None:
        regions = [
            (name, region) for name, region in sprite.regions.items() if not region.protected
        ]
    else:
        regions = [(region_param, _require_unprotected_region(sprite, region_param))]
    if not doc.palette.colors:
        raise OperationError(
            "repair_outline: the doc palette is empty; cannot pick an outline colour"
        )

    restore = op.params.get("restore")
    if restore is not None:
        restore_map = _as_dict(restore, "restore")
        for key, value in restore_map.items():
            region_name, _, shape_idx_raw = key.partition(":")
            shape_idx = int(shape_idx_raw)
            region_data = data.get("regions", {}).get(region_name)
            shapes_data = region_data.get("shapes") if isinstance(region_data, dict) else None
            if not isinstance(shapes_data, list) or not (0 <= shape_idx < len(shapes_data)):
                raise OperationError(f"repair_outline restore: unknown shape key {key!r}")
            prior = _as_dict(value, "restore entry")
            shapes_data[shape_idx]["rows"] = prior["rows"]
            shapes_data[shape_idx]["key"] = prior["key"]
        return "repair_outline", {k: v for k, v in op.params.items() if k != "restore"}

    outline_id = _outline_color_id(doc)
    outline_restore: dict[str, JSONValue] = {}
    for region_name, _region in regions:
        shapes_data = data["regions"][region_name]["shapes"]
        for shape_idx, shape_data in enumerate(shapes_data):
            if shape_data.get("op") != "bitmap":
                continue
            key = shape_data.get("key")
            if not isinstance(key, dict):
                continue
            outline_chars = {ch for ch, cid in key.items() if cid == outline_id}
            if not outline_chars:
                continue  # no authored outline in this bitmap; nothing to repair
            outline_char = sorted(outline_chars)[0]
            rows = list(shape_data["rows"])
            repaired_rows, changed = _repair_bitmap_rows(rows, outline_chars, outline_char)
            if changed:
                outline_restore[f"{region_name}:{shape_idx}"] = {
                    "rows": shape_data["rows"],
                    "key": shape_data["key"],
                }
                shape_data["rows"] = repaired_rows
                # A filled gap may leave a colour key unused by any row (e.g.
                # the interior fill colour that sat on the ring path). The
                # bitmap schema requires every key entry to be referenced, so
                # prune keys that no longer appear in any row.
                used_chars = {ch for row in repaired_rows for ch in row if ch != "."}
                key = shape_data.get("key")
                if isinstance(key, dict):
                    shape_data["key"] = {ch: cid for ch, cid in key.items() if ch in used_chars}
    return "repair_outline", {"region": region_param, "restore": outline_restore}


# --- variants (NOT a revision operation) --------------------------------------


def _static_region_names(sprite: SpriteAssetBase) -> set[str]:
    """Regions that must never move: named *shadow* or anchored at `feet`."""
    feet_anchor = "feet" if "feet" in sprite.anchors else None
    static: set[str] = set()
    for name, region in sprite.regions.items():
        if "shadow" in name.lower() or (feet_anchor is not None and region.anchor == feet_anchor):
            static.add(name)
    return static


def _region_swap_mapping(region: Region, palette_ids: list[str], salt: str) -> dict[str, str]:
    """Seeded, per-region injective old-id -> other-palette-id mapping."""
    used = sorted(
        {cid for shape in region.shapes for cid in _shape_color_ids(shape.model_dump(mode="json"))}
    )
    if not used or len(palette_ids) < 2:
        return {}
    seed_int = int(salt[0:2], 16) if len(salt) >= 2 else 0
    mapping: dict[str, str] = {}
    taken: set[str] = set()
    for idx, old in enumerate(used):
        if old not in palette_ids:
            continue
        start = (seed_int + idx) % len(palette_ids)
        chosen: str | None = None
        for step in range(len(palette_ids)):
            candidate = palette_ids[(start + step) % len(palette_ids)]
            if candidate != old and candidate not in taken:
                chosen = candidate
                break
        if chosen is None:
            continue
        mapping[old] = chosen
        taken.add(chosen)
    return mapping


def _build_variant(
    doc: AssetDocUnion,
    sprite: SpriteAssetBase,
    base_digest: str,
    variant_index: int,
    seen: set[str],
) -> AssetDocUnion:
    static = _static_region_names(sprite)
    palette_ids = [color.id for color in doc.palette.colors]
    for attempt in range(100):
        data = doc.model_dump(mode="json")
        for region_name, region in sprite.regions.items():
            if region.protected or region_name in static:
                continue
            digest = hashlib.sha256(
                f"{base_digest}:{variant_index}:{attempt}:{region_name}".encode()
            ).hexdigest()
            dx = (int(digest[0:2], 16) % 3) - 1
            dy = (int(digest[2:4], 16) % 3) - 1
            swap = int(digest[4:6], 16) % 2 == 0
            region_data = data["regions"][region_name]
            if dx != 0 or dy != 0:
                for shape_data in region_data["shapes"]:
                    _shift_shape(shape_data, dx, dy)
            if swap:
                mapping = _region_swap_mapping(region, palette_ids, digest[6:])
                if mapping:
                    for shape_data in region_data["shapes"]:
                        _remap_shape_colors(shape_data, mapping)
        try:
            new_doc = type(doc).model_validate(data)
        except ValidationError as exc:
            raise OperationError(
                f"generate_variants: variant {variant_index} failed schema validation: {exc}"
            ) from exc
        variant_hash = content_hash(new_doc)
        if variant_hash not in seen:
            return new_doc
    raise OperationError(
        f"generate_variants: could not produce a distinct variant {variant_index}"
    )


def generate_variants(doc: AssetDocUnion, count: int, seed: str) -> list[AssetDocUnion]:
    """`count` deterministic variants of `doc` (region colour swaps + small offsets).

    This is NOT a revision operation: it returns N fresh, validated docs and
    never mutates the input doc, never touches the revision log, and has no
    inverse. Each variant is derived from `seed` + its index via sha256, so the
    same (doc, count, seed) always yields the same docs; protected regions and
    shadow/feet-anchored regions are never modified. Variants keep the asset id
    so they are drop-in alternatives of the same asset.
    """
    if count < 1:
        raise OperationError(f"generate_variants: count must be >= 1, got {count}")
    if count > 64:
        raise OperationError(f"generate_variants: count must be <= 64, got {count}")
    if not isinstance(seed, str):
        raise OperationError(f"generate_variants: seed must be a string, got {seed!r}")
    sprite = _require_sprite_doc(doc)

    seen = {content_hash(doc)}
    base_digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    variants: list[AssetDocUnion] = []
    for index in range(count):
        variant = _build_variant(doc, sprite, base_digest, index, seen)
        variants.append(variant)
        seen.add(content_hash(variant))
    return variants


_HANDLERS: dict[str, Handler] = {
    "resize_region": _resize_region,
    "translate_region": _translate_region,
    "recolor_region": _recolor_region,
    "set_frame_duration": _set_frame_duration,
    "add_frame": _add_frame,
    "remove_frame": _remove_frame,
    "set_region_visibility": _set_region_visibility,
    "replace_spec": _replace_spec,
    "swap_palette": _swap_palette,
    "apply_material": _apply_material,
    "add_component": _add_component,
    "replace_component": _replace_component,
    "change_pose": _change_pose,
    "repair_outline": _repair_outline,
}


def apply_operation(doc: AssetDocUnion, op: OperationSpec) -> tuple[AssetDocUnion, OperationSpec]:
    """Apply `op` to `doc` and return `(new_doc, inverse_op)`.

    `doc` is never mutated: a handler works against a fresh
    `doc.model_dump(mode="json")`, and the result is re-validated back into
    `type(doc)` before being returned.
    """
    handler = _HANDLERS.get(op.name)
    if handler is None:
        raise OperationError(
            f"unknown operation {op.name!r}; available operations: {sorted(_HANDLERS)}"
        )
    data = doc.model_dump(mode="json")
    inverse_name, inverse_params = handler(doc, data, op)
    try:
        new_doc = type(doc).model_validate(data)
    except ValidationError as exc:
        raise OperationError(f"operation {op.name!r} produced an invalid document: {exc}") from exc

    check_protection(doc, new_doc, op.protect)

    inverse_op = OperationSpec(
        name=inverse_name, params=inverse_params, targets=op.targets, protect=op.protect
    )
    return new_doc, inverse_op


def affected_targets(doc: AssetDocUnion, op: OperationSpec) -> dict[str, list[str]]:
    """Best-effort report of the regions/animations/directions/frames `op` would touch.

    Read-only inspection of `op.params` (and `doc` for "all frames" cases); does
    not apply the operation. Used for dry-run reporting and partial rebuilds.
    """
    result: dict[str, list[str]] = {"regions": [], "animations": [], "directions": [], "frames": []}

    region = op.params.get("region")
    if isinstance(region, str):
        result["regions"] = [region]

    animation_name = op.params.get("animation")
    if isinstance(animation_name, str):
        result["animations"] = [animation_name]

    if op.name == "set_region_visibility":
        directions = op.params.get("directions")
        if isinstance(directions, list):
            result["directions"] = [d for d in directions if isinstance(d, str)]
        frames = op.params.get("frames")
        if isinstance(frames, list):
            result["frames"] = [
                str(f) for f in frames if isinstance(f, int) and not isinstance(f, bool)
            ]
    elif op.name == "set_frame_duration":
        frame = op.params.get("frame")
        if isinstance(frame, int) and not isinstance(frame, bool):
            result["frames"] = [str(frame)]
        elif (
            frame is None
            and isinstance(animation_name, str)
            and isinstance(doc, SpriteAssetBase)
            and animation_name in doc.animations
        ):
            result["frames"] = [str(i) for i in range(len(doc.animations[animation_name].frames))]
    elif op.name in ("add_frame", "remove_frame"):
        at = op.params.get("at")
        if isinstance(at, int) and not isinstance(at, bool):
            result["frames"] = [str(at)]
    elif op.name == "swap_palette":
        if isinstance(doc, SpriteAssetBase):
            result["regions"] = list(doc.regions)
    elif op.name in ("apply_material", "repair_outline"):
        if isinstance(region, str):
            result["regions"] = [region]
        elif isinstance(doc, SpriteAssetBase):
            result["regions"] = [name for name, r in doc.regions.items() if not r.protected]
    elif op.name in ("add_component", "replace_component"):
        component_name = op.params.get("component")
        if isinstance(component_name, str):
            try:
                spec = load_component(component_name)
            except OperationError:
                spec = None
            if spec is not None:
                result["regions"] = sorted(spec.regions)
        replace = op.params.get("replace")
        if isinstance(replace, list):
            result["regions"] = sorted(
                set(result["regions"]) | {r for r in replace if isinstance(r, str)}
            )
    elif op.name == "change_pose":
        if isinstance(animation_name, str):
            result["animations"] = [animation_name]
            frames = op.params.get("frames")
            if frames is None:
                if isinstance(doc, SpriteAssetBase) and animation_name in doc.animations:
                    result["frames"] = [
                        str(i) for i in range(len(doc.animations[animation_name].frames))
                    ]
            elif isinstance(frames, list):
                result["frames"] = [
                    str(f) for f in frames if isinstance(f, int) and not isinstance(f, bool)
                ]

    return result
