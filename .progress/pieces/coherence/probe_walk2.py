"""Probe 5: max-stride frames in west (side) and south_east (diagonal) views."""

import sys
import tempfile
from pathlib import Path

from pixel_forge import api
from pixel_forge.animation.cycles import generate_joint_walk_cycle
from pixel_forge.domain.palette import resolve_palette
from pixel_forge.rendering.direction import project_animated_frames

sys.path.insert(0, ".progress/pieces/coherence")
from make_demo import draw_layers

tmp = Path(tempfile.mkdtemp(prefix="forge-probe5-"))
proj_root = tmp / "proj"
api.init_project(proj_root, "coherence_probe5")

front = {}
for name, img in draw_layers().items():
    p = proj_root / "layers" / f"{name}.png"
    p.parent.mkdir(parents=True, exist_ok=True)
    img.save(p)
    front[name] = p

api.import_layered(proj_root, "hero", front, timestamp="2026-08-07T00:00:00Z")
doc = api.get_asset(proj_root, "hero")
palette = resolve_palette(doc.palette)
walk = generate_joint_walk_cycle(doc, {})
animated = project_animated_frames(doc, palette, walk)

for direction in ("west", "east", "south_east", "north_east"):
    frame = animated[direction][0]
    a = frame.array
    print(f"\n=== {direction} frame 0 (max stride, legs area y 30..48) ===")
    for y in range(30, 49):
        row = ""
        for x in range(a.shape[1]):
            px = tuple(a[y, x])
            row += "#" if px[3] > 0 else "."
        print(f"{y:2d} {row}")
