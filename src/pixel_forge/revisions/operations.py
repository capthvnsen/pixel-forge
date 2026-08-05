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
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, cast

from pydantic import ValidationError

from pixel_forge.animation.resolver import merge_transforms
from pixel_forge.errors import OperationError
from pixel_forge.schemas.animation import AnimationSpec, FrameSpec
from pixel_forge.schemas.asset import AssetDocUnion, SpriteAssetBase
from pixel_forge.schemas.common import Region, RegionTransform
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


_HANDLERS: dict[str, Handler] = {
    "resize_region": _resize_region,
    "translate_region": _translate_region,
    "recolor_region": _recolor_region,
    "set_frame_duration": _set_frame_duration,
    "add_frame": _add_frame,
    "remove_frame": _remove_frame,
    "set_region_visibility": _set_region_visibility,
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

    return result
