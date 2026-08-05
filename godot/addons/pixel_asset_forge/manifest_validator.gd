## Validates a parsed `*.forge.json` manifest dict BEFORE any Godot resource is built.
##
## Every check names the offending manifest file and field, so a failed import can be
## explained in plain language in the dock / headless summary rather than surfacing a
## raw Godot error. See docs/godot.md for the manifest contract this enforces.
@tool
class_name PixelForgeManifestValidator
extends RefCounted

const SUPPORTED_MANIFEST_VERSION := 1
const VALID_ASSET_TYPES: Array[String] = ["character", "enemy", "prop", "terrain"]
const REQUIRED_KEYS: Array[String] = ["manifest_version", "asset_id", "asset_type", "textures"]

## `asset_id` must be safe to join under `res://generated/<asset_id>/` with no traversal.
const _SAFE_ID_PATTERN := "^[A-Za-z0-9_-]+$"


class Result:
	var errors: Array[String] = []
	var warnings: Array[String] = []

	func is_ok() -> bool:
		return errors.is_empty()


## `manifest_dir` is the directory the manifest file itself lives in — texture paths in
## `textures` are resolved relative to it (mirrors how the golden fixtures ship a
## `<asset_id>/atlas.png` sibling next to `<asset_id>.forge.json`).
static func validate(manifest: Dictionary, manifest_path: String, manifest_dir: String) -> Result:
	var result := Result.new()

	for key in REQUIRED_KEYS:
		if not manifest.has(key):
			result.errors.append("%s: missing required key '%s'" % [manifest_path, key])

	var version = manifest.get("manifest_version")
	if version != SUPPORTED_MANIFEST_VERSION:
		# JSON numbers parse as float in GDScript, so a whole-number version like 2 would
		# otherwise print as "2.0" — display it the way the manifest author wrote it.
		var version_str := str(int(version)) if typeof(version) in [TYPE_INT, TYPE_FLOAT] else str(version)
		result.errors.append(
			"%s: unsupported manifest_version %s (this plugin supports version %d)"
			% [manifest_path, version_str, SUPPORTED_MANIFEST_VERSION]
		)

	var asset_type: String = str(manifest.get("asset_type", ""))
	if not VALID_ASSET_TYPES.has(asset_type):
		result.errors.append(
			"%s: asset_type '%s' is not one of %s"
			% [manifest_path, asset_type, str(VALID_ASSET_TYPES)]
		)

	var asset_id: String = str(manifest.get("asset_id", ""))
	var id_regex := RegEx.new()
	id_regex.compile(_SAFE_ID_PATTERN)
	if asset_id.is_empty() or id_regex.search(asset_id) == null:
		result.errors.append(
			(
				"%s: asset_id '%s' must match %s so generated output stays inside "
				+ "res://generated/<asset_id>/"
			)
			% [manifest_path, asset_id, _SAFE_ID_PATTERN]
		)

	var texture_sizes := _check_textures(manifest, manifest_path, manifest_dir, result)
	_check_sprite_frame_rects(manifest, manifest_path, texture_sizes, result)
	_check_tileset_bounds(manifest, manifest_path, texture_sizes, result)

	return result


## Returns texture logical-name -> pixel size, for every texture that exists and loads.
static func _check_textures(
	manifest: Dictionary, manifest_path: String, manifest_dir: String, result: Result
) -> Dictionary:
	var sizes: Dictionary = {}
	var textures = manifest.get("textures", {})
	if typeof(textures) != TYPE_DICTIONARY:
		return sizes

	for tex_name in textures.keys():
		var rel_path: String = str(textures[tex_name])
		var abs_path: String = manifest_dir.path_join(rel_path)
		if not FileAccess.file_exists(abs_path):
			result.errors.append(
				"%s: textures.%s references '%s' which does not exist at %s"
				% [manifest_path, tex_name, rel_path, abs_path]
			)
			continue
		var img := Image.new()
		var err := img.load(abs_path)
		if err != OK:
			result.errors.append(
				"%s: textures.%s at %s could not be read as an image (error %d)"
				% [manifest_path, tex_name, abs_path, err]
			)
			continue
		sizes[tex_name] = Vector2i(img.get_width(), img.get_height())

	return sizes


static func _rect_in_bounds(rect: Dictionary, bounds: Vector2i) -> bool:
	var x := int(rect.get("x", 0))
	var y := int(rect.get("y", 0))
	var w := int(rect.get("w", 0))
	var h := int(rect.get("h", 0))
	return w > 0 and h > 0 and x >= 0 and y >= 0 and (x + w) <= bounds.x and (y + h) <= bounds.y


static func _check_sprite_frame_rects(
	manifest: Dictionary, manifest_path: String, texture_sizes: Dictionary, result: Result
) -> void:
	var sprite_frames = manifest.get("sprite_frames", {})
	if typeof(sprite_frames) != TYPE_DICTIONARY or sprite_frames.is_empty():
		return
	if not texture_sizes.has("atlas"):
		return  # missing/unreadable texture already reported above
	var atlas_size: Vector2i = texture_sizes["atlas"]

	for anim_name in sprite_frames.keys():
		var anim = sprite_frames[anim_name]
		if typeof(anim) != TYPE_DICTIONARY:
			continue
		var frames = anim.get("frames", [])
		for i in frames.size():
			var rect = frames[i].get("rect", {})
			if not _rect_in_bounds(rect, atlas_size):
				result.errors.append(
					"%s: sprite_frames.%s.frames[%d].rect %s exceeds atlas bounds %s"
					% [manifest_path, anim_name, i, str(rect), str(atlas_size)]
				)


static func _check_tileset_bounds(
	manifest: Dictionary, manifest_path: String, texture_sizes: Dictionary, result: Result
) -> void:
	var tileset = manifest.get("tileset")
	if typeof(tileset) != TYPE_DICTIONARY:
		return
	if not texture_sizes.has("atlas"):
		return
	var atlas_size: Vector2i = texture_sizes["atlas"]

	var tile_size_arr = tileset.get("tile_size", [0, 0])
	var tw := int(tile_size_arr[0]) if tile_size_arr.size() > 0 else 0
	var th := int(tile_size_arr[1]) if tile_size_arr.size() > 1 else 0
	if tw <= 0 or th <= 0:
		result.errors.append(
			"%s: tileset.tile_size %s must be positive" % [manifest_path, str(tile_size_arr)]
		)
		return

	for tile in tileset.get("tiles", []):
		var x := int(tile.get("x", 0))
		var y := int(tile.get("y", 0))
		if x < 0 or y < 0 or (x + 1) * tw > atlas_size.x or (y + 1) * th > atlas_size.y:
			result.errors.append(
				(
					"%s: tileset.tiles tile_id '%s' at atlas coord (%d, %d) with tile_size "
					+ "%dx%d exceeds atlas bounds %s"
				)
				% [manifest_path, str(tile.get("tile_id", "?")), x, y, tw, th, str(atlas_size)]
			)
