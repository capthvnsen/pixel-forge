"""Chibi demo character styled to the Jephed top-down sample pack (20x32 cells,
~2.5 heads tall, 3-tone selout shading, top-left light). Layered front view:
hair, head, face, torso, arm_left, arm_right, leg_left, leg_right — the exact
custom-input contract (head/torso/arms/legs in separate layers).

Round-3 craft pass: rounded silhouette, textured hair (dither), expressive
2x3 eyes with highlights, a buttoned jacket with a placket, wider stance so
the walk's geometry clamp allows a real ~1.5px stride, and selout outlines
everywhere (no pure-black perimeters).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

CANVAS = (20, 32)

# ---- palette: 3-tone ramps per material, selout dark versions for outlines ----
INK = (24, 22, 30, 255)  # fallback (not used for visible outlines)
SKIN_HI = (240, 200, 160, 255)
SKIN_MID = (216, 168, 126, 255)
SKIN_LO = (178, 132, 96, 255)
SKIN_EDGE = (120, 84, 56, 255)  # selout around skin
HAIR_HI = (66, 187, 214, 255)  # bright cyan
HAIR_MID = (42, 143, 168, 255)
HAIR_LO = (28, 96, 122, 255)
HAIR_EDGE = (14, 44, 61, 255)  # dark navy selout for hair
SHIRT_HI = (228, 96, 104, 255)  # coral (warm highlight)
SHIRT_MID = (196, 62, 72, 255)
SHIRT_LO = (132, 28, 48, 255)  # cool maroon shadow (hue shift, sample style)
SHIRT_EDGE = (84, 16, 30, 255)  # dark maroon selout
GOLD = (232, 186, 96, 255)  # jacket buttons
GOLD_EDGE = (150, 108, 44, 255)
PANTS_HI = (110, 118, 134, 255)
PANTS_MID = (82, 88, 102, 255)
PANTS_LO = (50, 54, 68, 255)  # cooler shadow
PANTS_EDGE = (26, 28, 36, 255)
SHOE = (58, 52, 62, 255)
SHOE_HI = (92, 84, 98, 255)
SHOE_EDGE = (22, 20, 26, 255)
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


def _outline_rect(
    img: Image.Image, x0: int, y0: int, x1: int, y1: int, c: tuple, border: tuple | None = None
) -> None:
    _rect(img, x0, y0, x1, y1, c)
    b = border or INK
    for x in range(x0, x1 + 1):
        _px(img, x, y0, b)
        _px(img, x, y1, b)
    for y in range(y0, y1 + 1):
        _px(img, x0, y, b)
        _px(img, x1, y, b)


def _round_corners(img: Image.Image, x0: int, y0: int, x1: int, y1: int, color: tuple) -> None:
    """Trim the four corners of the rect to 1px — a rounded silhouette."""
    for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
        if img.getpixel((x, y)) == color:
            _px(img, x, y, (0, 0, 0, 0))


def draw_layers() -> dict[str, Image.Image]:
    layers = {
        k: _new()
        for k in ("hair", "head", "face", "torso", "arm_left", "arm_right", "leg_left", "leg_right")
    }

    # ============ HAIR (rows 0-5 dome, rows 4-12 curtains, dithered) ============
    hair = layers["hair"]
    _rect(hair, 5, 0, 14, 0, HAIR_HI)  # crown highlight
    _rect(hair, 4, 1, 15, 2, HAIR_MID)  # dome mid
    _rect(hair, 4, 3, 15, 5, HAIR_LO)  # dome lower
    _rect(hair, 3, 4, 4, 12, HAIR_LO)  # left curtain
    _rect(hair, 15, 4, 16, 12, HAIR_LO)  # right curtain
    # dither the curtains (clean 2px clusters, not 1x1 noise)
    _rect(hair, 3, 6, 4, 7, HAIR_MID)
    _rect(hair, 15, 6, 16, 7, HAIR_MID)
    _rect(hair, 3, 10, 4, 11, HAIR_MID)
    _rect(hair, 15, 10, 16, 11, HAIR_MID)
    # selout the dome + curtains
    for x in range(4, 16):
        _px(hair, x, 0, HAIR_EDGE)
    _px(hair, 3, 4, HAIR_EDGE)
    _px(hair, 16, 4, HAIR_EDGE)
    _px(hair, 3, 8, HAIR_EDGE)
    _px(hair, 16, 8, HAIR_EDGE)
    _px(hair, 3, 12, HAIR_EDGE)
    _px(hair, 16, 12, HAIR_EDGE)
    _px(hair, 4, 13, HAIR_EDGE)
    _px(hair, 15, 13, HAIR_EDGE)

    # ============ HEAD (rows 6-13, rounded, 12px) ============
    head = layers["head"]
    _rect(head, 4, 6, 15, 13, SKIN_MID)
    _rect(head, 4, 6, 7, 7, SKIN_HI)  # forehead highlight (top-left light)
    _rect(head, 4, 12, 15, 13, SKIN_LO)  # chin shadow — VISIBLE
    _px(head, 5, 8, SKIN_HI)  # cheek light
    # selout the face plane (dark warm-brown, sample style)
    for x in range(4, 16):
        _px(head, x, 6, SKIN_EDGE)
        _px(head, x, 13, SKIN_EDGE)
    for y in range(6, 14):
        _px(head, 4, y, SKIN_EDGE)
        _px(head, 15, y, SKIN_EDGE)
    _round_corners(head, 4, 6, 15, 13, SKIN_EDGE)  # rounded silhouette

    # ============ FACE (expressive 2x3 eyes + mouth) ============
    face = layers["face"]
    _rect(face, 7, 8, 8, 10, EYE)  # left eye 2x3
    _rect(face, 11, 8, 12, 10, EYE)  # right eye 2x3
    _px(face, 7, 8, EYE_HI)  # highlight top-left
    _px(face, 11, 8, EYE_HI)
    _rect(face, 8, 12, 11, 12, MOUTH)  # mouth line

    # ============ TORSO (rows 14-20, jacket with placket + buttons) ============
    torso = layers["torso"]
    _outline_rect(torso, 3, 14, 16, 20, SHIRT_MID, border=SHIRT_EDGE)
    _rect(torso, 4, 14, 6, 16, SHIRT_HI)  # top-left light on shoulder
    _rect(torso, 3, 19, 16, 20, SHIRT_LO)  # lower shadow
    _rect(torso, 9, 14, 10, 20, SHIRT_LO)  # centre placket shadow
    _px(torso, 9, 16, GOLD)  # buttons
    _px(torso, 9, 19, GOLD)
    _px(torso, 8, 15, SHIRT_HI)  # placket edge light
    _px(torso, 11, 15, SHIRT_HI)

    # ============ ARMS (3px wide, selout INK -> dark maroon) ============
    for side, x in (("arm_left", 2), ("arm_right", 15)):
        arm = layers[side]
        _rect(arm, x + 1, 14, x + 1, 19, SHIRT_MID)  # sleeve fill (centre column)
        _px(arm, x + 1, 14, SHIRT_HI)
        _rect(arm, x + 1, 20, x + 1, 22, SKIN_MID)  # hand
        _px(arm, x + 1, 20, SKIN_HI)  # hand highlight
        for y in range(14, 23):  # selout sleeve+hand
            _px(arm, x, y, SHIRT_EDGE if y < 20 else SKIN_EDGE)
            _px(arm, x + 2, y, SHIRT_EDGE if y < 20 else SKIN_EDGE)
        _px(arm, x, 22, SKIN_EDGE)
        _px(arm, x + 2, 22, SKIN_EDGE)

    # ============ LEGS (4px wide, WIDE stance, COMPACT — rows 21-30) ============
    for side, x in (("leg_left", 4), ("leg_right", 13)):
        leg = layers[side]
        _rect(leg, x + 1, 21, x + 2, 25, PANTS_MID)   # 2px fill (5px pants — chibi short legs)
        _px(leg, x + 1, 21, PANTS_HI)                 # thigh light
        _rect(leg, x + 1, 26, x + 2, 26, PANTS_LO)    # knee shadow
        _rect(leg, x + 1, 27, x + 2, 30, SHOE)        # shoe (4px tall — grounded)
        _px(leg, x + 1, 27, SHOE_HI)
        for y in range(21, 31):                       # selout pants + shoe
            _px(leg, x, y, PANTS_EDGE if y < 27 else SHOE_EDGE)
            _px(leg, x + 3, y, PANTS_EDGE if y < 27 else SHOE_EDGE)
        _px(leg, x, 30, SHOE_EDGE)
        _px(leg, x + 3, 30, SHOE_EDGE)
        _px(leg, x + 1, 21, PANTS_EDGE)

    # NOTE: deliberately NO ground shadow layer (would inflate limb bboxes and
    # break the walk's geometry clamps — the samples have no shadow either).
    return layers


def main() -> None:
    out = Path(__file__).resolve().parent / "chibi_layers"
    out.mkdir(parents=True, exist_ok=True)
    for name, img in draw_layers().items():
        img.save(out / f"{name}.png")
    print("chibi layers:", out)


if __name__ == "__main__":
    main()
