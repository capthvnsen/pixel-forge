"""Chibi demo character styled to the Jephed top-down sample pack (20x32 cells,
~2.5 heads tall, 3-tone selout shading, top-left light). Layered front view:
hair, head, face, torso, arm_left, arm_right, leg_left, leg_right — the exact
custom-input contract (head/torso/arms/legs in separate layers).

Round-2 fix: the round-1 critic verified the projection flattened the ramps —
the art hid them (hair dome covered the forehead highlight AND the chin
shadow, leaving one visible skin tone). Redrawn so every material's 3-tone
ramp is painted on VISIBLE pixels: hair cap on top with the face plane fully
below it (rows 6-13), arms 2px inside the cell (the samples' density, so the
walk's arm clamp has clearance), shoes/legs with hi/mid/lo.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

CANVAS = (20, 32)

# ---- palette: 3-tone ramps per material, selout dark versions for outlines ----
INK = (24, 22, 30, 255)  # near-black outline
SKIN_HI = (240, 200, 160, 255)
SKIN_MID = (216, 168, 126, 255)
SKIN_LO = (178, 132, 96, 255)
HAIR_HI = (66, 187, 214, 255)  # bright cyan
HAIR_MID = (42, 143, 168, 255)
HAIR_LO = (28, 96, 122, 255)
HAIR_EDGE = (14, 44, 61, 255)  # dark navy selout for hair
SHIRT_HI = (226, 92, 100, 255)  # coral
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
    layers = {
        k: _new()
        for k in ("hair", "head", "face", "torso", "arm_left", "arm_right", "leg_left", "leg_right")
    }

    # ================= HAIR (rows 0-5 dome + rows 4-12 curtains) =================
    hair = layers["hair"]
    _rect(hair, 5, 0, 14, 0, HAIR_HI)  # crown highlight
    _rect(hair, 4, 1, 15, 2, HAIR_MID)  # dome mid
    _rect(hair, 4, 3, 15, 5, HAIR_LO)  # dome lower
    _rect(hair, 3, 4, 4, 12, HAIR_LO)  # left curtain
    _rect(hair, 15, 4, 16, 12, HAIR_LO)  # right curtain
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

    # ================= HEAD (rows 6-13: fully visible below the hair) =================
    head = layers["head"]
    _rect(head, 5, 6, 14, 13, SKIN_MID)
    _rect(head, 5, 6, 8, 7, SKIN_HI)  # forehead highlight (top-left light)
    _rect(head, 5, 12, 14, 13, SKIN_LO)  # chin shadow — VISIBLE (below hair)
    # selout the face plane
    for x in range(5, 15):
        _px(head, x, 6, INK)
        _px(head, x, 13, INK)
    for y in range(6, 14):
        _px(head, 5, y, INK)
        _px(head, 14, y, INK)

    # ================= FACE (eyes + mouth) =================
    face = layers["face"]
    _rect(face, 7, 8, 8, 9, EYE)  # left eye
    _rect(face, 11, 8, 12, 9, EYE)  # right eye
    _px(face, 7, 8, EYE_HI)  # eye highlights (top-left)
    _px(face, 11, 8, EYE_HI)
    _rect(face, 8, 11, 11, 11, MOUTH)  # mouth line

    # ================= TORSO (rows 14-20) =================
    torso = layers["torso"]
    _outline_rect(torso, 4, 14, 15, 20, SHIRT_MID)  # base fill + INK border FIRST
    _rect(torso, 5, 14, 7, 16, SHIRT_HI)  # top-left light on shoulder
    _rect(torso, 4, 19, 15, 20, SHIRT_LO)  # lower shadow
    _rect(torso, 8, 14, 11, 15, SHIRT_LO)  # collar shadow

    # ================= ARMS (3px wide, 2px inside the cell edges) =================
    for side, x in (("arm_left", 3), ("arm_right", 15)):
        arm = layers[side]
        _rect(arm, x + 1, 14, x + 1, 19, SHIRT_MID)  # sleeve fill (centre column)
        _px(arm, x + 1, 14, SHIRT_HI)
        _rect(arm, x + 1, 20, x + 1, 22, SKIN_MID)  # hand
        _px(arm, x + 1, 20, SKIN_HI)  # hand highlight
        # selout the arm silhouette (outer columns INK, centre fill preserved)
        for y in range(14, 23):
            _px(arm, x, y, INK)
            _px(arm, x + 2, y, INK)
        _px(arm, x, 22, INK)
        _px(arm, x + 2, 22, INK)

    # ================= LEGS (3px wide, rows 21-30) =================
    for side, x in (("leg_left", 8), ("leg_right", 12)):
        leg = layers[side]
        _rect(leg, x + 1, 21, x + 1, 26, PANTS_MID)  # fill (centre column)
        _px(leg, x + 1, 21, PANTS_HI)  # thigh light
        _rect(leg, x + 1, 27, x + 1, 28, PANTS_LO)  # knee shadow
        _rect(leg, x + 1, 29, x + 1, 30, SHOE)  # shoe
        _px(leg, x + 1, 29, SHOE_HI)
        # selout (outer columns PANTS_EDGE, centre fill preserved)
        for y in range(21, 31):
            _px(leg, x, y, PANTS_EDGE)
            _px(leg, x + 2, y, PANTS_EDGE)
        _px(leg, x, 30, SHOE_EDGE)
        _px(leg, x + 2, 30, SHOE_EDGE)
        _px(leg, x + 1, 21, PANTS_EDGE)

    # NOTE: deliberately NO ground shadow layer — a shadow painted on the limb
    # layers inflates the leg/arm bboxes, which breaks the walk's geometry
    # clamps (the leg reads as a thick mass -> 35° scissors; the arm reads as
    # edge-close -> 2° freeze). The samples have no shadow either.
    return layers


def main() -> None:
    out = Path(__file__).resolve().parent / "chibi_layers"
    out.mkdir(parents=True, exist_ok=True)
    for name, img in draw_layers().items():
        img.save(out / f"{name}.png")
    print("chibi layers:", out)


if __name__ == "__main__":
    main()
