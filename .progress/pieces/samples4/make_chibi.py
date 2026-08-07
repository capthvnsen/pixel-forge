"""Chibi demo character styled to the Jephed top-down sample pack (20x32 cells,
~2.5 heads tall, 3-tone selout shading, top-left light). Layered front view:
hair, head, face, torso, arm_left, arm_right, leg_left, leg_right — the exact
custom-input contract (head/torso/arms/legs in separate layers).

Draws the SOUTH (front) view only; the engine projects the other directions.
"""
from __future__ import annotations

from pathlib import Path
from PIL import Image

CANVAS = (20, 32)

# ---- palette: 3-tone ramps per material, selout dark versions for outlines ----
INK = (24, 22, 30, 255)           # near-black outline
SKIN_HI = (240, 200, 160, 255)
SKIN_MID = (216, 168, 126, 255)
SKIN_LO = (178, 132, 96, 255)
HAIR_HI = (66, 187, 214, 255)     # bright cyan
HAIR_MID = (42, 143, 168, 255)
HAIR_LO = (28, 96, 122, 255)
HAIR_EDGE = (14, 44, 61, 255)     # dark navy selout for hair
SHIRT_HI = (226, 92, 100, 255)    # coral
SHIRT_MID = (196, 62, 72, 255)
SHIRT_LO = (150, 40, 52, 255)
SHIRT_EDGE = (90, 20, 28, 255)
PANTS_HI = (110, 118, 134, 255)
PANTS_MID = (82, 88, 102, 255)
PANTS_LO = (56, 60, 72, 255)
PANTS_EDGE = (28, 30, 38, 255)
SHOE = (58, 52, 62, 255)
SHOE_HI = (92, 84, 98, 255)
SHOE_EDGE = (24, 22, 28, 255)
EYE = (30, 24, 28, 255)
EYE_HI = (255, 255, 240, 255)
MOUTH = (120, 30, 42, 255)


def _new() -> Image.Image:
    return Image.new("RGBA", CANVAS, (0, 0, 0, 0))


def _px(img: Image.Image, x: int, y: int, c: tuple) -> None:
    if 0 <= x < CANVAS[0] and 0 <= y < CANVAS[1]:
        img.putpixel((x, y), c)


def _rect(img: Image.Image, x0: int, y0: int, x1: int, y1: int, c: tuple) -> None:
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            _px(img, x, y, c)


def _outline_rect(img: Image.Image, x0: int, y0: int, x1: int, y1: int, c: tuple) -> None:
    _rect(img, x0, y0, x1, y1, c)
    for x in range(x0, x1 + 1):
        _px(img, x, y0, INK)
        _px(img, x, y1, INK)
    for y in range(y0, y1 + 1):
        _px(img, x0, y, INK)
        _px(img, x1, y, INK)


def draw_layers() -> dict[str, Image.Image]:
    layers = {k: _new() for k in ("hair", "head", "face", "torso", "arm_left", "arm_right", "leg_left", "leg_right")}

    # ================= HAIR (rows 0-13): cap dome + side curtains =================
    hair = layers["hair"]
    _rect(hair, 4, 0, 15, 2, HAIR_HI)          # crown highlight
    _rect(hair, 3, 1, 16, 4, HAIR_MID)         # dome
    _rect(hair, 3, 5, 16, 9, HAIR_LO)          # dome sides
    _rect(hair, 3, 4, 3, 12, HAIR_LO)          # left curtain
    _rect(hair, 16, 4, 16, 12, HAIR_LO)        # right curtain
    _rect(hair, 4, 13, 15, 13, HAIR_EDGE)      # bottom edge selout
    # selout the dome silhouette
    for x in range(3, 17):
        _px(hair, x, 0, HAIR_EDGE)
    _px(hair, 2, 4, HAIR_EDGE)
    _px(hair, 17, 4, HAIR_EDGE)
    _px(hair, 2, 8, HAIR_EDGE)
    _px(hair, 17, 8, HAIR_EDGE)
    _px(hair, 2, 12, HAIR_EDGE)
    _px(hair, 17, 12, HAIR_EDGE)

    # ================= HEAD (rows 3-13): face plane =================
    head = layers["head"]
    _rect(head, 5, 4, 14, 12, SKIN_MID)
    _rect(head, 5, 4, 6, 6, SKIN_HI)           # top-left light on forehead
    _rect(head, 5, 13, 14, 13, SKIN_LO)        # chin shadow
    # selout the face plane
    for x in range(5, 15):
        _px(head, x, 4, INK)
        _px(head, x, 13, INK)
    for y in range(4, 14):
        _px(head, 5, y, INK)
        _px(head, 14, y, INK)

    # ================= FACE (eyes + mouth, rows 8-11) =================
    face = layers["face"]
    _rect(face, 7, 9, 8, 10, EYE)              # left eye (2x2)
    _rect(face, 11, 9, 12, 10, EYE)            # right eye
    _px(face, 7, 9, EYE_HI)                    # eye highlights (top-left)
    _px(face, 11, 9, EYE_HI)
    _rect(face, 8, 12, 11, 12, MOUTH)          # mouth line

    # ================= TORSO (rows 14-20): shirt, 3 tones =================
    torso = layers["torso"]
    _rect(torso, 4, 14, 15, 20, SHIRT_MID)
    _rect(torso, 5, 14, 6, 17, SHIRT_HI)       # top-left light on shoulder
    _rect(torso, 4, 18, 15, 20, SHIRT_LO)      # lower shadow
    _rect(torso, 8, 14, 11, 15, SHIRT_LO)      # collar shadow
    _outline_rect(torso, 4, 14, 15, 20, SHIRT_EDGE)

    # ================= ARMS (2px wide, at the sides, hands at hip) =================
    for side, x in (("arm_left", 3), ("arm_right", 15)):
        arm = layers[side]
        _rect(arm, x, 14, x + 1, 19, SHIRT_MID)   # sleeve
        _px(arm, x, 14, SHIRT_HI)
        _rect(arm, x, 20, x + 1, 22, SKIN_MID)    # hand
        _px(arm, x, 20, SKIN_HI)
        # selout the arm silhouette
        for y in range(14, 23):
            _px(arm, x, y, INK)
            _px(arm, x + 1, y, INK)
        _px(arm, x, 22, INK)
        _px(arm, x + 1, 22, INK)

    # ================= LEGS (rows 21-30): pants + shoes =================
    for side, x in (("leg_left", 7), ("leg_right", 11)):
        leg = layers[side]
        _rect(leg, x, 21, x + 2, 26, PANTS_MID)
        _px(leg, x, 21, PANTS_HI)                 # thigh light
        _rect(leg, x, 27, x + 2, 28, PANTS_LO)    # knee shadow
        _rect(leg, x, 29, x + 2, 30, SHOE)        # shoe
        _px(leg, x, 29, SHOE_HI)
        # selout
        for y in range(21, 31):
            _px(leg, x, y, PANTS_EDGE)
            _px(leg, x + 2, y, PANTS_EDGE)
        _px(leg, x, 30, SHOE_EDGE)
        _px(leg, x + 2, 30, SHOE_EDGE)
        _px(leg, x + 1, 21, PANTS_EDGE)

    # ground shadow (row 31)
    for l in layers.values():
        _rect(l, 6, 31, 13, 31, (30, 30, 40, 255))

    return layers


def main() -> None:
    out = Path(__file__).resolve().parent / "chibi_layers"
    out.mkdir(parents=True, exist_ok=True)
    for name, img in draw_layers().items():
        img.save(out / f"{name}.png")
    print("chibi layers:", out)


if __name__ == "__main__":
    main()
