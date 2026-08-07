"""Gauntlet builder: chibi front view -> engine 4 directions (south, west,
east, north) -> 3-frame walk -> sheet packed in the samples' row order
(down/left/right/up), 20x32 cells, x4 for review. This is 'ours' for the
gauntlet A/B against the Jephed sample pack references.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PIL import Image  # noqa: E402

sys.path.insert(0, "/Users/alex/orca/projects/Pixelartllm-buddy/.progress/pieces/samples4")
from make_chibi import draw_layers, CANVAS  # noqa: E402

from pixel_forge import api
from pixel_forge.animation.cycles import generate_joint_walk_cycle
from pixel_forge.domain.palette import resolve_palette
from pixel_forge.rendering.canvas import Canvas
from pixel_forge.rendering.direction import project_animated_frames, project_directions
from pixel_forge.schemas.animation import FrameSpec

OUT = Path("/Users/alex/orca/projects/Pixelartllm-buddy/.progress/pieces/samples4")
# samples' row order: down, left, right, up
ROW_DIRS = ("south", "west", "east", "north")


def build_doc():
    tmp = Path(tempfile.mkdtemp(prefix="gauntlet-chibi-"))
    root = tmp / "proj"
    api.init_project(root, "chibi")
    front = {}
    for role, img in draw_layers().items():
        p = root / "layers" / f"{role}.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        img.save(p)
        front[role] = p
    result = api.import_layered(root, "chibi", front, timestamp="2026-08-07T00:00:00Z")
    doc = api.get_asset(root, "chibi")
    palette = resolve_palette(doc.palette)
    return root, doc, palette, result


def rest_sheet_4dir(views, canvas, x4=True) -> Image.Image:
    """4 rows (south/west/east/north) x 1 frame, samples' row order."""
    w, h = canvas
    gap = 2
    sheet = Canvas(w + 2 * gap, 4 * h + 5 * gap)
    for r, d in enumerate(ROW_DIRS):
        sheet.blit(views[d].composite(canvas), (gap, gap + r * (h + gap)))
    img = sheet.to_image()
    if x4:
        img = img.resize((img.width * 4, img.height * 4), Image.Resampling.NEAREST)
    return img


def walk_sheet_4dir(animated, canvas, x4=True) -> Image.Image:
    """4 rows x 3 walk frames (L-stride, pass, R-stride) from the 8-frame cycle."""
    w, h = canvas
    gap = 2
    sheet = Canvas(3 * w + 4 * gap, 4 * h + 5 * gap)
    pick = {0, 2, 4}  # frames 0 (L stride), 2 (pass), 4 (R stride)
    for r, d in enumerate(ROW_DIRS):
        col = 0
        for fi, frame in enumerate(animated[d]):
            if fi not in pick:
                continue
            sheet.blit(frame, (gap + col * (w + gap), gap + r * (h + gap)))
            col += 1
    img = sheet.to_image()
    if x4:
        img = img.resize((img.width * 4, img.height * 4), Image.Resampling.NEAREST)
    return img


def main() -> None:
    root, doc, palette, result = build_doc()
    print(f"imported: canvas {result.canvas}, palette {result.palette_size}")
    canvas = tuple(doc.asset.canvas)
    print("canvas:", canvas)

    rest = project_directions(doc, palette)
    rest_sheet_4dir(rest, canvas).save(OUT / "ours_rest_4dir.png")

    walk = generate_joint_walk_cycle(doc, {})
    animated = project_animated_frames(doc, palette, walk)
    walk_sheet_4dir(animated, canvas).save(OUT / "ours_walk_4dir.png")

    # also an 8-frame walk for animation-quality forensics
    full = Canvas(8 * canvas[0] + 9 * 2, 4 * canvas[1] + 5 * 2)
    for r, d in enumerate(ROW_DIRS):
        for fi, frame in enumerate(animated[d]):
            full.blit(frame, (2 + fi * (canvas[0] + 2), 2 + r * (canvas[1] + 2)))
    full.scale(4).save_png(OUT / "ours_walk_8f.png")

    print("gates:", api.validate_asset(root, "chibi").blocking)
    print("ours sheets:", OUT / "ours_rest_4dir.png", OUT / "ours_walk_4dir.png")


if __name__ == "__main__":
    main()
