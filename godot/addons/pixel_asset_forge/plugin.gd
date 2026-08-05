## Pixel Asset Forge editor plugin. Targets Godot 4.4. Registers a dock that lists
## `*.forge.json` manifests under a configurable directory (default `res://forge/`) and
## imports them into native resources under `res://generated/<asset_id>/`.
## See docs/godot.md.
@tool
extends EditorPlugin

const DockScene = preload("res://addons/pixel_asset_forge/dock.tscn")

var _dock: Control


func _enter_tree() -> void:
	_dock = DockScene.instantiate()
	add_control_to_dock(DOCK_SLOT_LEFT_UR, _dock)


func _exit_tree() -> void:
	remove_control_from_docks(_dock)
	_dock.queue_free()
