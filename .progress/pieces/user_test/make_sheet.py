"""End-to-end: user's Aseprite front view -> canonical layers -> import ->
8 directions -> walk/idle/attack/jump animations -> sprite sheets.

Pre-processing: the aseprite has 6 frames (6 colour variants) x 6 layers
(Reference Layer + head+torso merged + left/right arms + left/right feet).
We pick frame 0, split the merged head+torso at its natural seam, rename to
the engine's canonical layer names, then drive the real product pipeline.
"""

from __future__ import annotations

import struct
import sys
import tempfile
from pathlib import Path

from PIL import Image

sys.path.insert(0, "/tmp")
from aseprite_parse import parse as parse_aseprite

from pixel_forge import api
from pixel_forge.animation.cycles import generate_joint_walk_cycle
from pixel_forge.domain.palette import resolve_palette
from pixel_forge.rendering.canvas import Canvas
from pixel_forge.rendering.direction import (
    project_animated_frames,
    project_directions,
)
from pixel_forge.schemas.animation import FrameSpec
from pixel_forge.schemas.common import RegionTransform, RotateSpec

OUT = Path("/Users/alex/orca/projects/Pixelartllm-buddy/.progress/pieces/user_test")
OUT.mkdir(parents=True, exist_ok=True)

ASEPRITE = Path("/Users/alex/Downloads/UntitledArtwork12.aseprite")
FRAME_IDX = 0  # pick the first colour variant


def export_layers(data, frame_infos, layers_names, depth, frame_idx):
    """Return {role: RGBA Image} for the chosen frame after canonical re-naming."""
    f = frame_infos[frame_idx]
    cels = {}  # layer index -> (x, y, img)
    for c in f["chunks"]:
        if c["type"] != 0x2005:
            continue
        p = c["data"]
        (lidx, cx, cy, _copacity, ctype, _cz) = struct.unpack_from("<hhHBBH", data, p)
        (w, h) = struct.unpack_from("<HH", data, p + 16)
        if ctype == 0:
            raw = data[p + 20 : p + 20 + w * h * 4]
            img = Image.frombytes("RGBA", (w, h), raw)
        elif ctype == 2:
            import zlib

            blob = data[p + 20 : p + c["size"] - 6]
            d = zlib.decompressobj()
            out = d.decompress(blob) + d.flush()
            img = Image.frombytes("RGBA", (w, h), out[: w * h * 4])
        else:
            continue
        cels[lidx] = (cx, cy, img)

    # Layer roles by index (verified by pixel forensics):
    # 0 = Reference Layer (skip), 1 = left foot, 2 = right foot,
    # 3 = merged head+torso, 4 = left arm, 5 = right arm
    merged = cels[3][2]
    head_full = merged.crop((0, 0, merged.width, 18))  # bezel + screen + chin
    torso = merged.crop((0, 18, merged.width, merged.height))
    # Split the front-only face detail (charcoal screen + cream eyes, rows 8-16)
    # into its own layer: the direction projection strips `face` regions from
    # back-facing views, so the robot's rear view is a clean bezel, not the screen.
    # The vacated screen area is repainted with the bezel's own fill colour (the
    # modal non-screen colour in rows 8-16) so the head silhouette stays solid.
    face = Image.new("RGBA", head_full.size, (0, 0, 0, 0))
    head = head_full.copy()
    CHARCOAL = (39, 39, 39, 255)
    CREAM_BRIGHT = (244, 230, 211, 255)
    px_head = head.load()
    px_face = face.load()
    screen_px = []
    fill_counts: dict[tuple[int, int, int, int], int] = {}
    for y in range(8, 17):
        for x in range(head.width):
            c = px_head[x, y]
            if c in (CHARCOAL, CREAM_BRIGHT):
                screen_px.append((x, y))
            elif c[3] > 0:
                fill_counts[c] = fill_counts.get(c, 0) + 1
    fill = max(fill_counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
    for x, y in screen_px:
        px_face[x, y] = px_head[x, y]
        px_head[x, y] = fill
    return {
        "head": head,
        "face": face,
        "torso": torso,
        "leg_left": cels[1][2],
        "leg_right": cels[2][2],
        "arm_left": cels[4][2],
        "arm_right": cels[5][2],
    }


def make_jump_frames() -> list:
    """Jump+fall: crouch -> launch -> apex -> fall -> land (regions bob + legs tuck)."""
    return [
        # crouch
        {
            "torso": RegionTransform(offset=(0, 1)),
            "head": RegionTransform(offset=(0, 1)),
            "leg_left": RegionTransform(offset=(0, 1), rotate=RotateSpec(angle_deg=8)),
            "leg_right": RegionTransform(offset=(0, 1), rotate=RotateSpec(angle_deg=-8)),
            "arm_left": RegionTransform(offset=(-1, 0)),
            "arm_right": RegionTransform(offset=(1, 0)),
        },
        # launch
        {
            "torso": RegionTransform(offset=(0, -2)),
            "head": RegionTransform(offset=(0, -2)),
            "leg_left": RegionTransform(offset=(0, -2), rotate=RotateSpec(angle_deg=18)),
            "leg_right": RegionTransform(offset=(0, -2), rotate=RotateSpec(angle_deg=-18)),
            "arm_left": RegionTransform(offset=(-1, -2)),
            "arm_right": RegionTransform(offset=(1, -2)),
        },
        # apex
        {
            "torso": RegionTransform(offset=(0, -4)),
            "head": RegionTransform(offset=(0, -4)),
            "leg_left": RegionTransform(offset=(0, -4), rotate=RotateSpec(angle_deg=28)),
            "leg_right": RegionTransform(offset=(0, -4), rotate=RotateSpec(angle_deg=-28)),
            "arm_left": RegionTransform(offset=(-1, -4)),
            "arm_right": RegionTransform(offset=(1, -4)),
        },
        # falling
        {
            "torso": RegionTransform(offset=(0, -3)),
            "head": RegionTransform(offset=(0, -3)),
            "leg_left": RegionTransform(offset=(0, -3), rotate=RotateSpec(angle_deg=12)),
            "leg_right": RegionTransform(offset=(0, -3), rotate=RotateSpec(angle_deg=-12)),
            "arm_left": RegionTransform(offset=(-1, -2)),
            "arm_right": RegionTransform(offset=(1, -2)),
        },
        # falling lower
        {
            "torso": RegionTransform(offset=(0, -1)),
            "head": RegionTransform(offset=(0, -1)),
            "leg_left": RegionTransform(offset=(0, -1), rotate=RotateSpec(angle_deg=4)),
            "leg_right": RegionTransform(offset=(0, -1), rotate=RotateSpec(angle_deg=-4)),
            "arm_left": RegionTransform(offset=(-1, -1)),
            "arm_right": RegionTransform(offset=(1, -1)),
        },
        # land
        {
            "torso": RegionTransform(offset=(0, 0)),
            "head": RegionTransform(offset=(0, 0)),
            "leg_left": RegionTransform(offset=(0, 0)),
            "leg_right": RegionTransform(offset=(0, 0)),
            "arm_left": RegionTransform(offset=(0, 0)),
            "arm_right": RegionTransform(offset=(0, 0)),
        },
    ]


def make_arm_swing_frames() -> list:
    """Swing-arm attack: shoulder-pivot wind-up, strike, recover (right arm)."""
    return [
        {"arm_right": RegionTransform(rotate=RotateSpec(angle_deg=-70))},
        {"arm_right": RegionTransform(rotate=RotateSpec(angle_deg=-110))},
        {"arm_right": RegionTransform(rotate=RotateSpec(angle_deg=45))},
        {"arm_right": RegionTransform(rotate=RotateSpec(angle_deg=10))},
        {"arm_right": RegionTransform(rotate=RotateSpec(angle_deg=0))},
    ]


def make_idle_frames() -> list:
    return [
        {"torso": RegionTransform(offset=(0, -1)), "head": RegionTransform(offset=(0, -1))},
        {"torso": RegionTransform(offset=(0, 0)), "head": RegionTransform(offset=(0, 0))},
    ]


def pack_sheet(animated, canvas_size, gap=2, x4=True):
    rows = list(animated.keys())
    cols = len(animated[rows[0]])
    w, h = canvas_size
    sheet = (
        Canvas(4 * w + 5 * gap, 2 * h + 3 * gap)
        if len(rows) == 8
        else Canvas(cols * w + (cols + 1) * gap, len(rows) * h + (len(rows) + 1) * gap)
    )
    if len(rows) == 8:
        # 8 directions in two rows: [NW N NE SE; W E SW S] like the demo
        layout = [
            ["north_west", "north", "north_east", "south_east"],
            ["west", "east", "south_west", "south"],
        ]
        for r, dirs in enumerate(layout):
            for col, d in enumerate(dirs):
                for fi, frame in enumerate(animated[d]):
                    x = gap + (col * 4 + fi) * (w + gap)
                    y = gap + r * (h + gap)
                    sheet.blit(frame, (x, y))
    else:
        for r, d in enumerate(rows):
            for fi, frame in enumerate(animated[d]):
                sheet.blit(frame, (gap + fi * (w + gap), gap + r * (h + gap)))
    img = sheet.to_image()
    if x4:
        img = img.resize((img.width * 4, img.height * 4), Image.NEAREST)
    return img


def main():
    data, frame_infos, _layers, _width, _height, _depth = parse_aseprite(str(ASEPRITE))
    layer_names = {}
    for c in frame_infos[0]["chunks"]:
        if c["type"] == 0x2004:
            (_lflags, _ltype, _lchild) = struct.unpack_from("<HHH", data, c["data"])
            (nlen,) = struct.unpack_from("<H", data, c["data"] + 16)
            name = data[c["data"] + 18 : c["data"] + 18 + nlen].decode()
            layer_names[len(layer_names)] = name
    print("aseprite layers:", layer_names)

    layers_out = export_layers(data, frame_infos, layer_names, _depth, FRAME_IDX)
    # --- real product flow ---
    proj_root = Path(tempfile.mkdtemp(prefix="robot-proj-"))
    api.init_project(proj_root, "robot")
    staging = proj_root / "layers"
    staging.mkdir(parents=True, exist_ok=True)
    for role, img in layers_out.items():
        img.save(staging / f"{role}.png")
        print(f"layer {role}: {img.size}")
    result = api.import_layered(
        proj_root,
        "bot",
        {r: staging / f"{r}.png" for r in layers_out},
        timestamp="2026-08-07T00:00:00Z",
    )
    doc = api.get_asset(proj_root, "bot")
    palette = resolve_palette(doc.palette)
    print(f"imported: canvas {result.canvas}, palette {result.palette_size}")

    # --- 8 directional rest poses ---
    rest = project_directions(doc, palette)
    canvas_size = tuple(doc.asset.canvas)
    print("canvas:", canvas_size)

    # --- animations ---
    anims = {
        "walk": project_animated_frames(doc, palette, generate_joint_walk_cycle(doc, {})),
        "idle": project_animated_frames(
            doc,
            palette,
            [FrameSpec(duration_ms=250, events=[], transforms=t) for t in make_idle_frames()],
        ),
        "jump": project_animated_frames(
            doc,
            palette,
            [FrameSpec(duration_ms=120, events=[], transforms=t) for t in make_jump_frames()],
        ),
        "arm_swing": project_animated_frames(
            doc,
            palette,
            [FrameSpec(duration_ms=100, events=[], transforms=t) for t in make_arm_swing_frames()],
        ),
    }

    for name, animated in anims.items():
        img = pack_sheet(animated, canvas_size)
        img.save(OUT / f"{name}_sheet.png")
        print(f"{name}: {len(animated['south'])} frames x 8 dirs -> {OUT / (name + '_sheet.png')}")

    # rest contact sheet
    rest_sheet = Canvas(4 * canvas_size[0] + 5 * 2, 2 * canvas_size[1] + 3 * 2)
    layout = [
        ["north_west", "north", "north_east", "south_east"],
        ["west", "east", "south_west", "south"],
    ]
    for r, dirs in enumerate(layout):
        for col, d in enumerate(dirs):
            rest_sheet.blit(
                rest[d].composite(canvas_size),
                (2 + col * (canvas_size[0] + 2), 2 + r * (canvas_size[1] + 2)),
            )
    img = rest_sheet.to_image()
    img.resize((img.width * 4, img.height * 4), Image.NEAREST).save(OUT / "rest_8dirs.png")

    # validation + determinism
    report = api.validate_asset(proj_root, "bot")
    walk2 = project_animated_frames(doc, palette, generate_joint_walk_cycle(doc, {}))
    det = all(
        all(a.equals(b) for a, b in zip(anims["walk"][d], walk2[d], strict=True))
        for d in anims["walk"]
    )
    print(f"validation blocking: {report.blocking} (findings {len(report.findings)})")
    print(f"determinism walk: {det}")
    print(f"OUT: {OUT}")


if __name__ == "__main__":
    main()
