## Builds native Godot resources from a validated `*.forge.json` manifest, entirely
## through Godot's own resource APIs, and saves them with `ResourceSaver.save`.
##
## Everything is written under `res://generated/<asset_id>/`. Reimporting the same
## asset id overwrites only that asset's own files, in place, at the same paths, so
## existing scene references keep working. See docs/godot.md for the full contract.
@tool
class_name PixelForgeImporter
extends RefCounted

const Validator = preload("res://addons/pixel_asset_forge/manifest_validator.gd")

const GENERATED_ROOT := "res://generated"
const _DEFAULT_MAX_FPS := 60.0

## Godot 4 `TileData` peering-bit enum, keyed by the same names
## `pixel_forge.exporters.godot.tileset.PEERING_BIT_NAMES` emits.
const _PEERING_BIT_ENUM := {
	"top_side": TileSet.CELL_NEIGHBOR_TOP_SIDE,
	"top_right_corner": TileSet.CELL_NEIGHBOR_TOP_RIGHT_CORNER,
	"right_side": TileSet.CELL_NEIGHBOR_RIGHT_SIDE,
	"bottom_right_corner": TileSet.CELL_NEIGHBOR_BOTTOM_RIGHT_CORNER,
	"bottom_side": TileSet.CELL_NEIGHBOR_BOTTOM_SIDE,
	"bottom_left_corner": TileSet.CELL_NEIGHBOR_BOTTOM_LEFT_CORNER,
	"left_side": TileSet.CELL_NEIGHBOR_LEFT_SIDE,
	"top_left_corner": TileSet.CELL_NEIGHBOR_TOP_LEFT_CORNER,
}

const _TERRAIN_MODE_ENUM := {
	"corners": TileSet.TERRAIN_MODE_MATCH_CORNERS,
	"edges": TileSet.TERRAIN_MODE_MATCH_SIDES,
	"corners_and_edges": TileSet.TERRAIN_MODE_MATCH_CORNERS_AND_SIDES,
}


class Outcome:
	var asset_id: String = ""
	var status: String = "failed"  # created | updated | unchanged | failed
	var errors: Array[String] = []
	var warnings: Array[String] = []
	var output_paths: Array[String] = []


static func import_manifest(manifest_path: String) -> Outcome:
	var outcome := Outcome.new()

	if not FileAccess.file_exists(manifest_path):
		outcome.errors.append("%s: file does not exist" % manifest_path)
		return outcome

	var parsed = JSON.parse_string(FileAccess.get_file_as_string(manifest_path))
	if typeof(parsed) != TYPE_DICTIONARY:
		outcome.errors.append("%s: could not be parsed as a JSON object" % manifest_path)
		return outcome
	var manifest: Dictionary = parsed
	outcome.asset_id = str(manifest.get("asset_id", ""))

	var manifest_dir := manifest_path.get_base_dir()
	var validation := Validator.validate(manifest, manifest_path, manifest_dir)
	outcome.errors.append_array(validation.errors)
	outcome.warnings.append_array(validation.warnings)
	if not validation.is_ok():
		return outcome

	var asset_id: String = manifest["asset_id"]
	var asset_type: String = manifest["asset_type"]
	var spec_hash: String = str(manifest.get("spec_hash", ""))
	var out_dir := "%s/%s" % [GENERATED_ROOT, asset_id]

	var dir_err := DirAccess.make_dir_recursive_absolute(out_dir)
	if dir_err != OK and dir_err != ERR_ALREADY_EXISTS:
		outcome.errors.append(
			"%s: could not create output directory %s (error %d)" % [manifest_path, out_dir, dir_err]
		)
		return outcome

	var previous_hash := _read_recorded_spec_hash(out_dir)
	var texture := _load_atlas_texture(manifest, manifest_dir, outcome)
	if texture == null:
		outcome.errors.append("%s: no usable texture in 'textures'" % manifest_path)
		return outcome

	match asset_type:
		"character", "enemy":
			_import_sprite_frames(manifest, texture, asset_id, out_dir, spec_hash, outcome)
		"prop":
			_import_animation_player(manifest, asset_id, out_dir, spec_hash, outcome)
		"terrain":
			_import_tileset(manifest, texture, asset_id, out_dir, spec_hash, outcome)
		_:
			outcome.errors.append("%s: unsupported asset_type '%s'" % [manifest_path, asset_type])

	if not outcome.errors.is_empty():
		outcome.status = "failed"
		return outcome

	_write_meta_resource(manifest, asset_id, out_dir, spec_hash, outcome)

	if previous_hash.is_empty():
		outcome.status = "created"
	elif previous_hash == spec_hash:
		outcome.status = "unchanged"
	else:
		outcome.status = "updated"
	return outcome


# --- shared helpers ----------------------------------------------------------------


static func _load_atlas_texture(
	manifest: Dictionary, manifest_dir: String, outcome: Outcome
) -> Texture2D:
	var textures = manifest.get("textures", {})
	if typeof(textures) != TYPE_DICTIONARY or textures.is_empty():
		return null
	var rel_path: String = str(textures.get("atlas", textures.values()[0]))
	var abs_path: String = manifest_dir.path_join(rel_path)
	var img := Image.new()
	var err := img.load(abs_path)
	if err != OK:
		outcome.errors.append("could not load texture %s (error %d)" % [abs_path, err])
		return null
	_apply_import_settings(abs_path, manifest)
	return ImageTexture.create_from_image(img)


## Best-effort only, and only ever an in-place patch of a `.import` file the editor
## has already generated: Godot 4 has no per-texture "nearest filter" import flag
## (filtering moved to CanvasItem.texture_filter / the project default -- see
## docs/godot.md), so the manifest's `import_settings` can only drive the import
## parameters that actually exist -- `mipmaps/generate` (no mipmaps), the
## `process/fix_alpha_border` toggle, and lossless `compress/mode`. The `filter`
## field is read here (never silently ignored) but has nothing to patch: 'nearest'
## is honoured at the project level (`rendering/textures/canvas_textures/
## default_texture_filter=0` in the sample project) and per-node where this
## importer constructs CanvasItems (sample-map layers get
## `TEXTURE_FILTER_NEAREST`). We deliberately do NOT fabricate a `.import` file
## from scratch for a texture the editor has never imported -- Godot 4.3+'s import
## file format carries a resource `uid` that a hand-rolled file can get wrong, and
## getting it wrong is worse than leaving the file absent for the editor to
## generate normally on first scan. This has no bearing on the correctness of the
## generated resources themselves, which read the PNG bytes directly via
## `Image`/`ImageTexture`, not through the editor's cached import.
static func _apply_import_settings(abs_texture_path: String, manifest: Dictionary) -> void:
	var import_path := abs_texture_path + ".import"
	if not FileAccess.file_exists(import_path):
		return
	var cfg := ConfigFile.new()
	if cfg.load(import_path) != OK:
		return
	var settings: Dictionary = manifest.get("import_settings", {})
	# Consumed for documentation/guarding: the schema only allows "nearest", which
	# Godot 4 honours via project setting + CanvasItem.texture_filter, not via the
	# import file. A future schema widening past "nearest" would need per-node
	# TEXTURE_FILTER_* handling in the generated scenes instead.
	var filter_mode: String = str(settings.get("filter", "nearest"))
	if filter_mode != "nearest":
		return
	cfg.set_value("params", "mipmaps/generate", bool(settings.get("mipmaps", false)))
	cfg.set_value("params", "process/fix_alpha_border", bool(settings.get("fix_alpha_border", false)))
	if str(settings.get("compress_mode", "lossless")) == "lossless":
		cfg.set_value("params", "compress/mode", 0)
	cfg.save(import_path)


static func _read_recorded_spec_hash(out_dir: String) -> String:
	if not DirAccess.dir_exists_absolute(out_dir):
		return ""
	for file_name in DirAccess.get_files_at(out_dir):
		if file_name.ends_with("_meta.tres"):
			var res: Resource = ResourceLoader.load(
				out_dir.path_join(file_name), "", ResourceLoader.CACHE_MODE_IGNORE
			)
			if res:
				return str(res.get_meta("spec_hash", ""))
	return ""


## Companion resource carrying pivots/baseline/events/spec_hash as plain `set_meta`
## entries — the one place a game script (or a future reimport) can always find this
## data regardless of asset_type, since props build several `Animation` resources with
## no single natural "the generated resource" to hang metadata off of.
static func _write_meta_resource(
	manifest: Dictionary, asset_id: String, out_dir: String, spec_hash: String, outcome: Outcome
) -> void:
	var meta := Resource.new()
	meta.set_meta("asset_id", asset_id)
	meta.set_meta("asset_type", str(manifest.get("asset_type", "")))
	meta.set_meta("spec_hash", spec_hash)
	var baseline_y = manifest.get("baseline_y")
	if baseline_y != null:
		meta.set_meta("baseline_y", baseline_y)
	meta.set_meta("pivots", manifest.get("pivots", {}))
	meta.set_meta("events", manifest.get("events", {}))
	# Procedural shader specs are reserved for future shader-material construction;
	# surfacing them as meta is their one consumption today (game code can read
	# them without the plugin building any shader).
	meta.set_meta("procedural", manifest.get("procedural", {}))

	var path := "%s/%s_meta.tres" % [out_dir, asset_id]
	var err := ResourceSaver.save(meta, path)
	if err != OK:
		outcome.errors.append("failed to save %s (error %d)" % [path, err])
	else:
		outcome.output_paths.append(path)


static func _to_variant(value: Variant) -> Variant:
	if typeof(value) == TYPE_ARRAY and value.size() == 2:
		return Vector2(float(value[0]), float(value[1]))
	return value


# --- fps derivation, ported bit-for-bit from exporters/godot/spriteframes.py --------


static func _gcd(a: int, b: int) -> int:
	a = abs(a)
	b = abs(b)
	while b != 0:
		var t := b
		b = a % b
		a = t
	return a


## Mirrors `derive_fps`: `fps = min(1000 / gcd(durations_ms), max_fps)`.
static func _derive_fps(durations: Array, max_fps: float = _DEFAULT_MAX_FPS) -> float:
	if durations.is_empty():
		return max_fps
	var gcd_ms: int = int(durations[0])
	for d in durations:
		gcd_ms = _gcd(gcd_ms, int(d))
	if gcd_ms <= 0:
		return max_fps
	return minf(1000.0 / float(gcd_ms), max_fps)


## Mirrors `duration_frames_for`: `duration_ms * fps / 1000`.
static func _duration_frames_for(duration_ms: int, fps: float) -> float:
	return duration_ms * fps / 1000.0


# --- character / enemy: SpriteFrames ------------------------------------------------


static func _import_sprite_frames(
	manifest: Dictionary,
	texture: Texture2D,
	asset_id: String,
	out_dir: String,
	spec_hash: String,
	outcome: Outcome,
) -> void:
	var sf_data = manifest.get("sprite_frames", {})
	if typeof(sf_data) != TYPE_DICTIONARY or sf_data.is_empty():
		outcome.warnings.append("%s: sprite_frames is empty, nothing to import" % asset_id)
		return

	var sprite_frames := SpriteFrames.new()
	if sprite_frames.has_animation("default"):
		sprite_frames.remove_animation("default")

	for anim_name in sf_data.keys():
		var anim = sf_data[anim_name]
		var frames = anim.get("frames", [])
		var durations: Array = []
		for f in frames:
			durations.append(int(f.get("duration_ms", 0)))
		var fps := _derive_fps(durations)

		sprite_frames.add_animation(anim_name)
		sprite_frames.set_animation_loop(anim_name, bool(anim.get("loop", true)))
		sprite_frames.set_animation_speed(anim_name, fps)

		for i in frames.size():
			var rect = frames[i].get("rect", {})
			var atlas_tex := AtlasTexture.new()
			atlas_tex.atlas = texture
			atlas_tex.region = Rect2(
				int(rect.get("x", 0)), int(rect.get("y", 0)), int(rect.get("w", 0)), int(rect.get("h", 0))
			)
			# SpriteFrames has no standalone `set_frame_duration` setter in Godot 4.4/4.7 —
			# only `get_frame_duration` plus `add_frame`/`set_frame`, both of which take the
			# duration multiplier alongside the texture. Pass it at creation time instead.
			var duration := _duration_frames_for(int(frames[i].get("duration_ms", 0)), fps)
			sprite_frames.add_frame(anim_name, atlas_tex, duration)

	sprite_frames.set_meta("asset_id", asset_id)
	sprite_frames.set_meta("spec_hash", spec_hash)
	var path := "%s/%s_sprite_frames.tres" % [out_dir, asset_id]
	var err := ResourceSaver.save(sprite_frames, path)
	if err != OK:
		outcome.errors.append("failed to save %s (error %d)" % [path, err])
	else:
		outcome.output_paths.append(path)


# --- props: AnimationPlayer Animation resources -------------------------------------


## `node_path` is `"<animation>/<region>"` or `"<animation>/<direction>/<region>"`; the
## first segment selects which `Animation` resource the track belongs to, the rest is
## the track's own NodePath within that animation.
##
## The manifest's `animation_player.animations` dict is the authoritative list of
## `Animation` resources to build: each entry carries `total_duration_ms` (the exact
## sum of the animation's frame durations, so `Animation.length` includes the last
## frame's hold — keyframe `time_ms` values are cumulative *start* times and would
## truncate it) and `loop`. An animation with zero keyframe tracks (colour-swap-only
## or fully static regions) still gets a resource with the correct length and loop
## mode, plus a warning — without this it would silently vanish. Manifests without
## the `animations` dict (pre-duration-fix) fall back to grouping tracks and to the
## last keyframe time, preserving the old behaviour.
static func _import_animation_player(
	manifest: Dictionary, asset_id: String, out_dir: String, spec_hash: String, outcome: Outcome
) -> void:
	var ap_data = manifest.get("animation_player")
	if typeof(ap_data) != TYPE_DICTIONARY:
		outcome.warnings.append("%s: animation_player is absent, nothing to import" % asset_id)
		return
	var animations_data = ap_data.get("animations", {})
	var tracks = ap_data.get("tracks", [])

	var grouped: Dictionary = {}  # animation name -> Array[Dictionary]
	for track in tracks:
		var node_path: String = str(track.get("node_path", ""))
		var parts := node_path.split("/")
		if parts.size() < 2:
			outcome.errors.append(
				(
					"%s: animation_player track node_path '%s' must be "
					+ "'<animation>/<region>' or '<animation>/<direction>/<region>'"
				)
				% [asset_id, node_path]
			)
			continue
		var anim_name: String = parts[0]
		var sub_path: String = "/".join(parts.slice(1))
		if not grouped.has(anim_name):
			grouped[anim_name] = []
		grouped[anim_name].append(
			{
				"sub_path": sub_path,
				"property": str(track.get("property", "")),
				"keyframes": track.get("keyframes", []),
			}
		)

	# Authoritative animation list from the manifest when present; otherwise fall
	# back to the animations that produced tracks (pre-duration-fix manifests).
	var anim_names: Array = []
	if typeof(animations_data) == TYPE_DICTIONARY and not animations_data.is_empty():
		anim_names = animations_data.keys()
	else:
		anim_names = grouped.keys()

	var sprite_frames_data = manifest.get("sprite_frames", {})

	for anim_name in anim_names:
		var animation := Animation.new()
		var anim_meta: Dictionary = {}
		if typeof(animations_data) == TYPE_DICTIONARY and animations_data.has(anim_name):
			anim_meta = animations_data[anim_name]
		var loop := bool(anim_meta.get("loop", _loop_for_animation(sprite_frames_data, anim_name)))
		animation.loop_mode = Animation.LOOP_LINEAR if loop else Animation.LOOP_NONE

		# True end-to-end length: total_duration_ms from the manifest when present
		# (JSON numbers parse as float in GDScript). Fallback for pre-fix manifests:
		# the last keyframe's start time (the old, truncating behaviour).
		var length_sec := 0.001
		var total_ms: Variant = anim_meta.get("total_duration_ms", -1)
		if typeof(total_ms) in [TYPE_INT, TYPE_FLOAT] and float(total_ms) > 0:
			length_sec = maxf(length_sec, float(total_ms) / 1000.0)
		for t in grouped.get(anim_name, []):
			var track_idx := animation.add_track(Animation.TYPE_VALUE)
			animation.track_set_path(track_idx, NodePath("%s:%s" % [t["sub_path"], t["property"]]))
			for kf in t["keyframes"]:
				var time_sec := float(kf.get("time_ms", 0)) / 1000.0
				length_sec = maxf(length_sec, time_sec)
				animation.track_insert_key(track_idx, time_sec, _to_variant(kf.get("value")))
		animation.length = length_sec

		if not grouped.has(anim_name):
			outcome.warnings.append(
				(
					"%s: animation '%s' has no keyframe tracks (colour-swap-only or "
					+ "static pose); emitting a zero-track Animation of length %.3fs so "
					+ "it exists and loops"
				)
				% [asset_id, anim_name, length_sec]
			)

		animation.set_meta("asset_id", asset_id)
		animation.set_meta("spec_hash", spec_hash)
		var path := "%s/%s_%s.anim.tres" % [out_dir, asset_id, anim_name]
		var err := ResourceSaver.save(animation, path)
		if err != OK:
			outcome.errors.append("failed to save %s (error %d)" % [path, err])
		else:
			outcome.output_paths.append(path)


## The manifest schema has no dedicated "loop" field for `animation_player` tracks.
## `sprite_frames` entries are named `<animation>_<direction>` and do carry `loop`, so we
## reuse that as the animation's loop signal when a matching entry exists. Defaults to
## looping (true) when no such entry is present — see docs/godot.md known limitations.
static func _loop_for_animation(sprite_frames_data: Variant, anim_name: String) -> bool:
	if typeof(sprite_frames_data) != TYPE_DICTIONARY:
		return true
	for key in sprite_frames_data.keys():
		if str(key).begins_with(anim_name + "_"):
			var entry = sprite_frames_data[key]
			if typeof(entry) == TYPE_DICTIONARY:
				return bool(entry.get("loop", true))
	return true


# --- terrain: TileSet -----------------------------------------------------------------


static func _import_tileset(
	manifest: Dictionary,
	texture: Texture2D,
	asset_id: String,
	out_dir: String,
	spec_hash: String,
	outcome: Outcome,
) -> void:
	var ts_data = manifest.get("tileset")
	if typeof(ts_data) != TYPE_DICTIONARY:
		outcome.errors.append("%s: asset_type terrain requires a 'tileset' payload" % asset_id)
		return

	var tile_size_arr = ts_data.get("tile_size", [16, 16])
	var tw := int(tile_size_arr[0])
	var th := int(tile_size_arr[1])

	var atlas_source := TileSetAtlasSource.new()
	atlas_source.texture = texture
	atlas_source.texture_region_size = Vector2i(tw, th)

	var tile_set := TileSet.new()
	tile_set.tile_size = Vector2i(tw, th)
	var source_id: int = tile_set.add_source(atlas_source)

	var coord_by_id: Dictionary = {}
	for tile in ts_data.get("tiles", []):
		var coords := Vector2i(int(tile.get("x", 0)), int(tile.get("y", 0)))
		var tile_id: String = str(tile.get("tile_id", ""))
		if not atlas_source.has_tile(coords):
			atlas_source.create_tile(coords)
		coord_by_id[tile_id] = coords

	_apply_terrain_sets(ts_data, tile_set, atlas_source, coord_by_id, asset_id, outcome)
	_apply_animated_tiles(ts_data, atlas_source, asset_id, outcome)
	_apply_tile_physics(ts_data, tile_set, atlas_source, coord_by_id, asset_id, outcome)

	tile_set.set_meta("asset_id", asset_id)
	tile_set.set_meta("spec_hash", spec_hash)
	var tile_set_path := "%s/%s_tileset.tres" % [out_dir, asset_id]
	var err := ResourceSaver.save(tile_set, tile_set_path)
	if err != OK:
		outcome.errors.append("failed to save %s (error %d)" % [tile_set_path, err])
	else:
		outcome.output_paths.append(tile_set_path)

	var sample_map = ts_data.get("sample_map")
	if typeof(sample_map) == TYPE_DICTIONARY:
		_build_sample_map_scene(sample_map, tile_set, source_id, asset_id, out_dir, outcome)


## Registers terrain sets/terrains and applies peering bits from the manifest's
## pre-resolved `terrain_bits` (tile id -> peering-bit name -> terrain name) — never
## re-derived from `transitions`, per the exporter contract. Terrain names come from the
## union of each set's own `tiles` list and every terrain name that appears as a
## `terrain_bits` value; the schema does not separately export "this whole tile IS
## terrain X", so a tile's own `.terrain` is left unassigned (-1) and only its explicit
## peering bits are set. See docs/godot.md known limitations.
static func _apply_terrain_sets(
	ts_data: Dictionary,
	tile_set: TileSet,
	atlas_source: TileSetAtlasSource,
	coord_by_id: Dictionary,
	asset_id: String,
	outcome: Outcome,
) -> void:
	var terrain_bits_data: Dictionary = ts_data.get("terrain_bits", {})
	var terrain_sets_data = ts_data.get("terrain_sets", {})
	if typeof(terrain_sets_data) != TYPE_DICTIONARY:
		return

	for set_name in terrain_sets_data.keys():
		var set_data: Dictionary = terrain_sets_data[set_name]
		tile_set.add_terrain_set()
		var set_idx: int = tile_set.get_terrain_sets_count() - 1
		var mode_key := str(set_data.get("mode", "corners_and_edges"))
		tile_set.set_terrain_set_mode(
			set_idx, _TERRAIN_MODE_ENUM.get(mode_key, TileSet.TERRAIN_MODE_MATCH_CORNERS_AND_SIDES)
		)

		var terrain_names: Array = []
		for tile_id in set_data.get("tiles", []):
			if not terrain_names.has(tile_id):
				terrain_names.append(tile_id)
			for terrain_name in terrain_bits_data.get(tile_id, {}).values():
				if not terrain_names.has(terrain_name):
					terrain_names.append(terrain_name)
		terrain_names.sort()

		var terrain_idx: Dictionary = {}  # terrain name -> int
		for terrain_name in terrain_names:
			tile_set.add_terrain(set_idx)
			var t_idx: int = tile_set.get_terrains_count(set_idx) - 1
			tile_set.set_terrain_name(set_idx, t_idx, terrain_name)
			terrain_idx[terrain_name] = t_idx

		for tile_id in set_data.get("tiles", []):
			if not coord_by_id.has(tile_id):
				outcome.warnings.append(
					"%s: terrain_sets.%s references unknown tile_id '%s'" % [asset_id, set_name, tile_id]
				)
				continue
			var tile_data: TileData = atlas_source.get_tile_data(coord_by_id[tile_id], 0)
			tile_data.terrain_set = set_idx
			for peering_name in terrain_bits_data.get(tile_id, {}).keys():
				if not _PEERING_BIT_ENUM.has(peering_name):
					outcome.warnings.append(
						"%s: unknown peering bit '%s' on tile '%s'" % [asset_id, peering_name, tile_id]
					)
					continue
				var to_terrain: String = terrain_bits_data[tile_id][peering_name]
				if terrain_idx.has(to_terrain):
					tile_data.set_terrain_peering_bit(_PEERING_BIT_ENUM[peering_name], terrain_idx[to_terrain])


## Applies the manifest's per-tile physics metadata -- `collision_tiles`,
## `navigation_tiles`, `occlusion_tiles` -- by creating one TileSet layer per
## feature (physics / navigation / occlusion) and stamping every listed tile with a
## full-tile rectangle shape. The exporter pre-resolves every id to a base tile
## (an animated tile's non-base frames collapse onto their base frame, the only
## coordinate Godot registers as a real tile), so a listed id either exists in
## `coord_by_id` or is a manifest bug worth a warning, never a silent skip of the
## whole feature. A full-tile rect is the one shape derivable from the manifest
## alone; per-tile polygon art is not part of the schema.
static func _apply_tile_physics(
	ts_data: Dictionary,
	tile_set: TileSet,
	atlas_source: TileSetAtlasSource,
	coord_by_id: Dictionary,
	asset_id: String,
	outcome: Outcome,
) -> void:
	var collision_tiles: Array = ts_data.get("collision_tiles", [])
	var navigation_tiles: Array = ts_data.get("navigation_tiles", [])
	var occlusion_tiles: Array = ts_data.get("occlusion_tiles", [])
	if collision_tiles.is_empty() and navigation_tiles.is_empty() and occlusion_tiles.is_empty():
		return

	var tile_size: Vector2i = tile_set.tile_size
	var tile_rect := PackedVector2Array(
		[
			Vector2(0, 0),
			Vector2(tile_size.x, 0),
			Vector2(tile_size.x, tile_size.y),
			Vector2(0, tile_size.y),
		]
	)

	if not collision_tiles.is_empty():
		tile_set.add_physics_layer()
		var physics_layer: int = tile_set.get_physics_layers_count() - 1
		for tile_id in collision_tiles:
			var coords = coord_by_id.get(str(tile_id))
			if coords == null:
				outcome.warnings.append(
					"%s: collision_tiles references unknown tile_id '%s'" % [asset_id, tile_id]
				)
				continue
			var tile_data: TileData = atlas_source.get_tile_data(coords, 0)
			tile_data.add_collision_polygon(physics_layer)
			var poly_idx: int = tile_data.get_collision_polygons_count(physics_layer) - 1
			tile_data.set_collision_polygon_points(physics_layer, poly_idx, tile_rect)

	if not navigation_tiles.is_empty():
		tile_set.add_navigation_layer()
		var navigation_layer: int = tile_set.get_navigation_layers_count() - 1
		var nav_poly := NavigationPolygon.new()
		nav_poly.set_vertices(tile_rect)
		nav_poly.add_polygon(PackedInt32Array([0, 1, 2, 3]))
		for tile_id in navigation_tiles:
			var coords = coord_by_id.get(str(tile_id))
			if coords == null:
				outcome.warnings.append(
					"%s: navigation_tiles references unknown tile_id '%s'" % [asset_id, tile_id]
				)
				continue
			var tile_data: TileData = atlas_source.get_tile_data(coords, 0)
			tile_data.set_navigation_polygon(navigation_layer, nav_poly)

	if not occlusion_tiles.is_empty():
		tile_set.add_occlusion_layer()
		var occlusion_layer: int = tile_set.get_occlusion_layers_count() - 1
		var occluder := OccluderPolygon2D.new()
		occluder.closed = true
		occluder.polygon = tile_rect
		for tile_id in occlusion_tiles:
			var coords = coord_by_id.get(str(tile_id))
			if coords == null:
				outcome.warnings.append(
					"%s: occlusion_tiles references unknown tile_id '%s'" % [asset_id, tile_id]
				)
				continue
			var tile_data: TileData = atlas_source.get_tile_data(coords, 0)
			tile_data.set_occluder(occlusion_layer, occluder)


## Godot's `TileSetAtlasSource` animation model requires an animated tile's frames to
## occupy a contiguous horizontal strip of atlas cells starting at the tile's own
## coordinates, entirely inside the atlas texture, and otherwise empty; calling the
## animation setters when that doesn't hold either throws an engine-level error (printed
## straight to the console, not a catchable GDScript error) or leaves the tile in a
## half-set state. `pixel_forge.exporters.godot.tileset` already guarantees the frames a
## manifest lists are contiguous (raising `ExportError` at build time otherwise), but
## this plugin treats every manifest as untrusted input, so it re-checks contiguity,
## atlas bounds, and strip emptiness itself, before calling into the engine, and reports
## a hard `outcome.errors` entry (not a warning) when a manifest violates it; a manifest
## that can't be represented must fail the import, not silently ship without its
## animation. See docs/godot.md known limitations.
static func _apply_animated_tiles(
	ts_data: Dictionary, atlas_source: TileSetAtlasSource, asset_id: String, outcome: Outcome
) -> void:
	var animated_tiles_data = ts_data.get("animated_tiles", {})
	if typeof(animated_tiles_data) != TYPE_DICTIONARY:
		return

	var tile_size: Vector2i = atlas_source.texture_region_size
	var atlas_size := Vector2i(0, 0)
	if atlas_source.texture:
		atlas_size = atlas_source.texture.get_size()

	for anim_name in animated_tiles_data.keys():
		var anim_data: Dictionary = animated_tiles_data[anim_name]
		var frames = anim_data.get("frames", [])
		if frames.is_empty():
			continue
		var first_coords := Vector2i(int(frames[0].get("x", 0)), int(frames[0].get("y", 0)))

		var problem := _animated_tile_infeasibility_reason(
			frames, first_coords, tile_size, atlas_size, atlas_source
		)
		if not problem.is_empty():
			outcome.errors.append(
				(
					"%s: animated_tiles.%s frames %s cannot be represented as a Godot tile "
					+ "animation (%s); it requires a contiguous %d-cell horizontal strip "
					+ "starting at %s, empty except for the base tile, inside the %s atlas"
				)
				% [
					asset_id,
					anim_name,
					str(frames.map(func(f): return [int(f.get("x", 0)), int(f.get("y", 0))])),
					problem,
					frames.size(),
					str(first_coords),
					str(atlas_size),
				]
			)
			continue

		if not atlas_source.has_tile(first_coords):
			atlas_source.create_tile(first_coords)
		atlas_source.set_tile_animation_frames_count(first_coords, frames.size())
		atlas_source.set_tile_animation_columns(first_coords, 0)
		var duration_sec := float(anim_data.get("frame_duration_ms", 200)) / 1000.0
		for i in frames.size():
			atlas_source.set_tile_animation_frame_duration(first_coords, i, duration_sec)

		# Read the frame count back: if the engine rejected the call above for a reason
		# this pre-check didn't anticipate, it fails silently from GDScript's point of
		# view (only a raw console ERROR:, no catchable exception). This is what turns
		# that into a build failure instead of a tileset that's quietly missing its
		# animation.
		var applied_count := atlas_source.get_tile_animation_frames_count(first_coords)
		if applied_count != frames.size():
			outcome.errors.append(
				(
					"%s: animated_tiles.%s: the engine reports %d animation frame(s) for the "
					+ "tile at %s after import, expected %d; the generated tileset does not "
					+ "carry the animation"
				)
				% [asset_id, anim_name, applied_count, str(first_coords), frames.size()]
			)


## Empty string when `frames` (one animated tile's manifest frame list, already known
## non-empty) can be safely applied to `atlas_source`; a short human-readable reason
## otherwise. Three checks, in order: contiguous horizontal strip starting at
## `first_coords`; strip fits inside `atlas_size`; every non-base cell in the strip is
## not already a registered tile (Godot refuses to resize an occupied strip, and only
## reports that as a raw engine error, never a catchable one, so it must be caught here
## instead).
static func _animated_tile_infeasibility_reason(
	frames: Array,
	first_coords: Vector2i,
	tile_size: Vector2i,
	atlas_size: Vector2i,
	atlas_source: TileSetAtlasSource,
) -> String:
	for i in frames.size():
		var expected := Vector2i(first_coords.x + i, first_coords.y)
		var actual := Vector2i(int(frames[i].get("x", 0)), int(frames[i].get("y", 0)))
		if actual != expected:
			return "not a contiguous horizontal strip"

	var fits: bool = (
		(first_coords.x + frames.size()) * tile_size.x <= atlas_size.x
		and (first_coords.y + 1) * tile_size.y <= atlas_size.y
	)
	if not fits:
		return "does not fit inside the atlas"

	for i in range(1, frames.size()):
		var strip_coords := Vector2i(first_coords.x + i, first_coords.y)
		if atlas_source.has_tile(strip_coords):
			return "cell %s in the strip is already occupied by another tile" % str(strip_coords)

	return ""


## Demo `TileMapLayer` scene built straight from `sample_map`, one layer node per key.
static func _build_sample_map_scene(
	sample_map: Dictionary,
	tile_set: TileSet,
	source_id: int,
	asset_id: String,
	out_dir: String,
	outcome: Outcome,
) -> void:
	var root := Node2D.new()
	root.name = asset_id

	var layers = sample_map.get("layers", {})
	for layer_name in layers.keys():
		var layer := TileMapLayer.new()
		layer.name = str(layer_name)
		layer.tile_set = tile_set
		layer.texture_filter = CanvasItem.TEXTURE_FILTER_NEAREST
		var rows = layers[layer_name]
		for y in rows.size():
			var row = rows[y]
			for x in row.size():
				var coord = row[x]
				layer.set_cell(Vector2i(x, y), source_id, Vector2i(int(coord[0]), int(coord[1])))
		root.add_child(layer)
		layer.owner = root

	var packed := PackedScene.new()
	var pack_err := packed.pack(root)
	if pack_err != OK:
		outcome.errors.append("%s: failed to pack sample map scene (error %d)" % [asset_id, pack_err])
		root.free()
		return

	var scene_path := "%s/%s_sample_map.tscn" % [out_dir, asset_id]
	var save_err := ResourceSaver.save(packed, scene_path)
	if save_err != OK:
		outcome.errors.append("%s: failed to save %s (error %d)" % [asset_id, scene_path, save_err])
	else:
		outcome.output_paths.append(scene_path)
	root.free()
