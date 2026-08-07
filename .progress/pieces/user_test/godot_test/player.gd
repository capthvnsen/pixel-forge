extends CharacterBody2D

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
