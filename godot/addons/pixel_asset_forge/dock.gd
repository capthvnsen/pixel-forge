## Editor dock: lists `*.forge.json` manifests under a configurable directory, shows
## each asset's id/type/spec_hash, and imports them one at a time or all at once.
## Built entirely in code (rather than hand-authored child nodes in dock.tscn) so the
## UI tree can't drift out of sync with this script.
@tool
extends Control

const Importer = preload("res://addons/pixel_asset_forge/importer.gd")

const DEFAULT_MANIFEST_DIR := "res://forge/"

var manifest_dir: String = DEFAULT_MANIFEST_DIR

var _dir_edit: LineEdit
var _list_box: VBoxContainer
var _log: RichTextLabel


func _ready() -> void:
	_build_ui()
	refresh()


func _build_ui() -> void:
	var margin := MarginContainer.new()
	margin.set_anchors_preset(Control.PRESET_FULL_RECT)
	for side in ["margin_left", "margin_right", "margin_top", "margin_bottom"]:
		margin.add_theme_constant_override(side, 6)
	add_child(margin)

	var root_vbox := VBoxContainer.new()
	root_vbox.size_flags_vertical = Control.SIZE_EXPAND_FILL
	margin.add_child(root_vbox)

	var title := Label.new()
	title.text = "Pixel Asset Forge"
	root_vbox.add_child(title)

	var dir_row := HBoxContainer.new()
	root_vbox.add_child(dir_row)
	_dir_edit = LineEdit.new()
	_dir_edit.text = manifest_dir
	_dir_edit.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_dir_edit.text_submitted.connect(func(_t: String): refresh())
	dir_row.add_child(_dir_edit)
	var refresh_btn := Button.new()
	refresh_btn.text = "Refresh"
	refresh_btn.pressed.connect(refresh)
	dir_row.add_child(refresh_btn)

	var import_all_btn := Button.new()
	import_all_btn.text = "Import All"
	import_all_btn.pressed.connect(_on_import_all_pressed)
	root_vbox.add_child(import_all_btn)

	var scroll := ScrollContainer.new()
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	scroll.custom_minimum_size = Vector2(0, 160)
	root_vbox.add_child(scroll)
	_list_box = VBoxContainer.new()
	_list_box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(_list_box)

	var log_label := Label.new()
	log_label.text = "Import log"
	root_vbox.add_child(log_label)
	_log = RichTextLabel.new()
	_log.custom_minimum_size = Vector2(0, 140)
	_log.bbcode_enabled = true
	_log.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_log.scroll_following = true
	root_vbox.add_child(_log)


func refresh() -> void:
	manifest_dir = _dir_edit.text if _dir_edit.text.strip_edges() != "" else DEFAULT_MANIFEST_DIR
	for child in _list_box.get_children():
		child.queue_free()

	var manifests := _find_manifests(manifest_dir)
	if manifests.is_empty():
		var empty_label := Label.new()
		empty_label.text = "No *.forge.json manifests found under %s" % manifest_dir
		_list_box.add_child(empty_label)
		return

	for path in manifests:
		_add_manifest_row(path)


func _find_manifests(dir_path: String) -> Array[String]:
	var found: Array[String] = []
	var dir := DirAccess.open(dir_path)
	if dir == null:
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


func _add_manifest_row(path: String) -> void:
	var row := HBoxContainer.new()
	_list_box.add_child(row)

	var parsed = JSON.parse_string(FileAccess.get_file_as_string(path))
	var label := Label.new()
	if typeof(parsed) == TYPE_DICTIONARY:
		var d: Dictionary = parsed
		var spec_hash := str(d.get("spec_hash", "?"))
		label.text = "%s [%s] hash=%s" % [
			d.get("asset_id", "?"), d.get("asset_type", "?"), spec_hash.left(12)
		]
	else:
		label.text = "%s (invalid JSON)" % path
	label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(label)

	var import_btn := Button.new()
	import_btn.text = "Import"
	import_btn.pressed.connect(_import_one.bind(path))
	row.add_child(import_btn)


func _on_import_all_pressed() -> void:
	for path in _find_manifests(manifest_dir):
		_import_one(path)


func _import_one(path: String) -> void:
	var outcome := Importer.import_manifest(path)
	_append_log(path, outcome)
	if Engine.is_editor_hint():
		# EditorInterface is a global singleton in the editor context -- unlike
		# EditorPlugin.get_editor_interface(), it's reachable from a plain Control.
		EditorInterface.get_resource_filesystem().scan()


func _append_log(path: String, outcome) -> void:
	var status_color := Color.RED if not outcome.errors.is_empty() else Color.LIME_GREEN
	_log.push_color(status_color)
	_log.add_text("%s: %s\n" % [path, outcome.status])
	_log.pop()
	for e in outcome.errors:
		_log.push_color(Color.RED)
		_log.add_text("  ERROR: %s\n" % e)
		_log.pop()
	for w in outcome.warnings:
		_log.push_color(Color.ORANGE)
		_log.add_text("  WARNING: %s\n" % w)
		_log.pop()
