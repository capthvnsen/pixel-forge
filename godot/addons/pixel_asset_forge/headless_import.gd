## Headless CLI entry point: imports every `*.forge.json` manifest found under a
## directory, printing a summary, and exits non-zero if any manifest fails validation
## or import. Run via tools/godot_headless_import.sh — see docs/godot.md.
##
## `manifest_dir` may be a `res://` path or a plain OS filesystem path (DirAccess and
## FileAccess accept both), so it does not need to live inside this Godot project.
extends SceneTree

const Importer = preload("res://addons/pixel_asset_forge/importer.gd")

const DEFAULT_MANIFEST_DIR := "res://forge"


func _initialize() -> void:
	var manifest_dir := _parse_manifest_dir(OS.get_cmdline_user_args())

	print("Pixel Asset Forge headless import")
	print("Manifest directory: %s" % manifest_dir)

	var manifests := _find_manifests(manifest_dir)
	print("Found %d manifest(s)" % manifests.size())

	var ok_count := 0
	var failed_count := 0
	for path in manifests:
		var outcome := Importer.import_manifest(path)
		if outcome.errors.is_empty():
			ok_count += 1
			print("[%s] %s" % [outcome.status.to_upper(), path])
		else:
			failed_count += 1
			print("[FAILED] %s" % path)
		for warning in outcome.warnings:
			print("  WARNING: %s" % warning)
		for error in outcome.errors:
			print("  ERROR: %s" % error)

	print("---")
	print("%d imported, %d failed, %d total" % [ok_count, failed_count, manifests.size()])

	quit(1 if failed_count > 0 else 0)


func _parse_manifest_dir(args: PackedStringArray) -> String:
	for arg in args:
		if arg.begins_with("--manifest-dir="):
			return arg.substr("--manifest-dir=".length())
	return DEFAULT_MANIFEST_DIR


func _find_manifests(dir_path: String) -> Array[String]:
	var found: Array[String] = []
	var dir := DirAccess.open(dir_path)
	if dir == null:
		print("Manifest directory not found (treating as 0 manifests): %s" % dir_path)
		return found
	dir.list_dir_begin()
	var entry_name := dir.get_next()
	while entry_name != "":
		if not dir.current_is_dir() and entry_name.ends_with(".forge.json"):
			found.append(dir_path.path_join(entry_name))
		entry_name = dir.get_next()
	dir.list_dir_end()
	found.sort()
	return found
