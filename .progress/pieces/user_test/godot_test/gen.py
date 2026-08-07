"""Generate a super-basic Godot 4 test project for the robot's 8-direction
sheets: Player scene + script (WASD/arrows = move, Shift = run, Space = jump,
J = swing) + one SpriteFrames resource with all 40 animations, from the 1x
sheets produced by the same pipeline as the review PNGs.

Run:  uv run python .progress/pieces/user_test/godot_test/gen.py
Then: godot --path .progress/pieces/user_test/godot_test
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/Users/alex/orca/projects/Pixelartllm-buddy/.progress/pieces/user_test")
import make_sheet  # noqa: E402  (reuses parse + import + anims + pack_sheet)

OUT = Path(__file__).resolve().parent
SHEETS = OUT / "sheets"
FRAMES = OUT / "frames"
CELL = (27, 31)
GAP = 2
LAYOUT = [
    ["north_west", "north", "north_east", "south_east"],
    ["west", "east", "south_west", "south"],
]
DIRS = ("north_west", "north", "north_east", "south_east", "west", "east", "south_west", "south")

# frame durations (s) per animation, matching make_sheet.py's FrameSpecs
DUR = {
    "walk": [0.1] * 8,
    "run": [0.07] * 8,
    "idle": [0.25, 0.25],
    "jump": [0.12] * 6,
    "swing": [0.1] * 5,
}
LOOP = {"walk": True, "run": True, "idle": True, "jump": False, "swing": False}
ANIM_ORDER = ("walk", "run", "idle", "jump", "swing")


def build_1x_sheets() -> dict[str, Path]:
    """Re-run the pipeline and save 1x sheets into the project's sheets/."""
    from pixel_forge import api
    from pixel_forge.animation.cycles import generate_joint_walk_cycle
    from pixel_forge.domain.palette import resolve_palette
    from pixel_forge.rendering.direction import project_animated_frames
    from pixel_forge.schemas.animation import FrameSpec

    data, frame_infos, layer_names, _w, _h, _depth = make_sheet.parse_aseprite(
        make_sheet.ASEPRITE
    )
    tmp = Path(tempfile.mkdtemp(prefix="forge-godot-test-"))
    proj_root = tmp / "proj"
    api.init_project(proj_root, "bot")
    front = {}
    for role, img in make_sheet.export_layers(
        data, frame_infos, layer_names, _depth, make_sheet.FRAME_IDX
    ).items():
        p = proj_root / "layers" / f"{role}.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        img.save(p)
        front[role] = p
    api.import_layered(proj_root, "bot", front, timestamp="2026-08-07T00:00:00Z")
    doc = api.get_asset(proj_root, "bot")
    palette = resolve_palette(doc.palette)
    canvas = tuple(doc.asset.canvas)

    anims = {
        "walk": project_animated_frames(doc, palette, generate_joint_walk_cycle(doc, {})),
        "run": project_animated_frames(
            doc,
            palette,
            generate_joint_walk_cycle(
                doc, {"duration_ms": 70, "bob": 2, "lift": 3, "joint_swing": 40}
            ),
        ),
        "idle": project_animated_frames(
            doc,
            palette,
            [
                FrameSpec(duration_ms=250, events=[], transforms=t)
                for t in make_sheet.make_idle_frames()
            ],
        ),
        "jump": project_animated_frames(
            doc,
            palette,
            [
                FrameSpec(duration_ms=120, events=[], transforms=t)
                for t in make_sheet.make_jump_frames()
            ],
        ),
        "swing": project_animated_frames(
            doc,
            palette,
            [
                FrameSpec(duration_ms=100, events=[], transforms=t)
                for t in make_sheet.make_arm_swing_frames()
            ],
        ),
    }
    SHEETS.mkdir(parents=True, exist_ok=True)
    sheets = {}
    for name, animated in anims.items():
        img = make_sheet.pack_sheet(animated, canvas, x4=False)
        p = SHEETS / f"{name}_sheet.png"
        img.save(p)
        sheets[name] = p
        print("sheet:", p, img.size)
    return sheets


def region_for(sheet_name: str, direction: str, frame: int) -> tuple[int, int, int, int]:
    """(x, y, w, h) region of `direction`'s `frame` in the 2-row sheet layout."""
    del sheet_name
    for r, dirs in enumerate(LAYOUT):
        for c, d in enumerate(dirs):
            if d == direction:
                x = GAP + (c * 4 + frame) * (CELL[0] + GAP)
                y = GAP + r * (CELL[1] + GAP)
                return (x, y, *CELL)
    raise KeyError(direction)


def write_spriteframes(sheets: dict[str, Path]) -> Path:
    FRAMES.mkdir(parents=True, exist_ok=True)
    ext = [
        f'[ext_resource type="Texture2D" path="res://sheets/{name}_sheet.png" id="{name}"]'
        for name in ANIM_ORDER
    ]
    sub: list[str] = []
    anims: list[str] = []
    atlas_id = 0
    for name in ANIM_ORDER:
        for d in DIRS:
            entries = []
            for fi in range(len(DUR[name])):
                rx, ry, rw, rh = region_for(name, d, fi)
                sub.append(
                    f'[sub_resource type="AtlasTexture" id="AtlasTexture_{atlas_id}"]\n'
                    f"atlas = ExtResource(\"{name}\")\n"
                    f"region = Rect2({rx}, {ry}, {rw}, {rh})"
                )
                entries.append(
                    f'"duration": {DUR[name][fi]}, '
                    + f'"texture": SubResource("AtlasTexture_{atlas_id}")'
                )
                atlas_id += 1
            loop = "true" if LOOP[name] else "false"
            anims.append(
                "{\n"
                '"frames": [{\n'
                + ",\n".join(entries)
                + "\n}],\n"
                + f'"loop": {loop},\n'
                + f'"name": &"{name}_{d}",\n'
                + '"speed": 1.0\n'
                "}"
            )
    load_steps = len(ext) + len(sub) + 1
    p = FRAMES / "robot.tres"
    p.write_text(
        f'[gd_resource type="SpriteFrames" load_steps="{load_steps}" format="3"]\n\n'
        + "\n\n".join(ext)
        + "\n\n"
        + "\n\n".join(sub)
        + "\n\n[resource]\n"
        + "animations = [\n"
        + ",\n".join(anims)
        + "\n]\n"
    )
    print("frames:", p)
    return p


def write_project() -> None:
    (OUT / "project.godot").write_text(
        'config_version=5\n'
        '\n'
        '[application]\n'
        'config/name="Pixel Forge Robot Test"\n'
        'run/main_scene="res://Player.tscn"\n'
        'config/features=PackedStringArray("4.4")\n'
        '\n'
        '[display]\n'
        'window/size/viewport_width=960\n'
        'window/size/viewport_height=540\n'
        'window/stretch/mode="canvas_items"\n'
        'window/stretch/aspect="keep"\n'
        '\n'
        '[rendering]\n'
        'textures/canvas_textures/default_texture_filter=0\n'
    )


def write_scene() -> None:
    (OUT / "Player.tscn").write_text(
        '[gd_scene load_steps="4" format="3"]\n'
        '\n'
        '[ext_resource type="Script" path="res://player.gd" id="1"]\n'
        '[ext_resource type="SpriteFrames" path="res://frames/robot.tres" id="2"]\n'
        '\n'
        '[sub_resource type="RectangleShape2D" id="RectangleShape2D_1"]\n'
        "size = Vector2(12, 18)\n"
        '\n'
        '[node name="Player" type="CharacterBody2D"]\n'
        'script = ExtResource("1")\n'
        '\n'
        '[node name="AnimatedSprite2D" type="AnimatedSprite2D" parent="."]\n'
        "scale = Vector2(4, 4)\n"
        'sprite_frames = ExtResource("2")\n'
        'animation = &"idle_south"\n'
        '\n'
        '[node name="CollisionShape2D" type="CollisionShape2D" parent="."]\n'
        'shape = SubResource("RectangleShape2D_1")\n'
        '\n'
        '[node name="Camera2D" type="Camera2D" parent="."]\n'
    )


def write_script() -> None:
    (OUT / "player.gd").write_text(
        '''extends CharacterBody2D

# Super-basic 8-direction test driver for the Pixel Forge robot sheets.
# Move: WASD / arrows   Run: hold Shift   Jump: Space   Swing: J

const SPEED := 150.0
const RUN_MULT := 1.9

@onready var anim: AnimatedSprite2D = $AnimatedSprite2D

var _facing := "south"
var _busy := ""

func _ready() -> void:
    _setup_input()
    anim.play("idle_" + _facing)

func _setup_input() -> void:
    for action in ["move_left", "move_right", "move_up", "move_down", "run", "jump", "swing"]:
        if not InputMap.has_action(action):
            InputMap.add_action(action)
    _bind("move_left", KEY_A); _bind("move_left", KEY_LEFT)
    _bind("move_right", KEY_D); _bind("move_right", KEY_RIGHT)
    _bind("move_up", KEY_W); _bind("move_up", KEY_UP)
    _bind("move_down", KEY_S); _bind("move_down", KEY_DOWN)
    _bind("run", KEY_SHIFT)
    _bind("jump", KEY_SPACE)
    _bind("swing", KEY_J)

func _bind(action: String, key: Key) -> void:
    var ev := InputEventKey.new()
    ev.physical_keycode = key
    InputMap.action_add_event(action, ev)

func _dir_name(dir: Vector2) -> String:
    var names := ["east", "south_east", "south", "south_west",
                  "west", "north_west", "north", "north_east"]
    var idx := int(round(dir.angle() / TAU * 8.0)) % 8
    return names[idx]

func _physics_process(_delta: float) -> void:
    var dir := Input.get_vector("move_left", "move_right", "move_up", "move_down")
    if dir.length() > 0.0:
        _facing = _dir_name(dir)
    var running := Input.is_action_pressed("run")
    velocity = dir * SPEED * (RUN_MULT if running else 1.0)
    move_and_slide()

    if _busy != "":
        return
    if Input.is_action_just_pressed("jump"):
        _play_once("jump")
    elif Input.is_action_just_pressed("swing"):
        _play_once("swing")
    elif dir.length() > 0.0:
        anim.play(("run_" if running else "walk_") + _facing)
    else:
        anim.play("idle_" + _facing)

func _play_once(kind: String) -> void:
    _busy = kind
    anim.play(kind + "_" + _facing)
    anim.animation_finished.connect(_on_anim_finished, CONNECT_ONE_SHOT)

func _on_anim_finished() -> void:
    _busy = ""
    anim.play("idle_" + _facing)
'''
    )


def main() -> None:
    shutil.rmtree(SHEETS, ignore_errors=True)
    shutil.rmtree(FRAMES, ignore_errors=True)
    sheets = build_1x_sheets()
    write_spriteframes(sheets)
    write_project()
    write_scene()
    write_script()
    print("Godot project:", OUT)


if __name__ == "__main__":
    main()
