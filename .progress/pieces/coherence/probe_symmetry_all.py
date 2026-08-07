"""Probe 7: all mirror-pair walk symmetry + west max-stride leg separation."""

import sys
import tempfile
from pathlib import Path

from pixel_forge import api
from pixel_forge.animation.cycles import generate_joint_walk_cycle
from pixel_forge.domain.palette import resolve_palette
from pixel_forge.rendering.direction import project_animated_frames

sys.path.insert(0, ".progress/pieces/coherence")
from make_demo import draw_layers

tmp = Path(tempfile.mkdtemp(prefix="forge-probe7-"))
proj_root = tmp / "proj"
api.init_project(proj_root, "coherence_probe7")

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

pairs = (
    ("west", "east"),
    ("south_west", "south_east"),
    ("north_west", "north_east"),
    ("north", "south"),
)
for a, b in pairs:
    ok = all(animated[a][i].equals(animated[b][i].mirror_x()) for i in range(len(walk)))
    print(f"{a} walk == mirror({b} walk): {ok}")

# west max-stride legs (frame 0) — previously merged
frame = animated["west"][0]
a = frame.array
print("\n=== west frame 0 (max stride, legs y 30..48) ===")
for y in range(30, 49):
    row = ""
    for x in range(a.shape[1]):
        px = tuple(a[y, x])
        row += "#" if px[3] > 0 else "."
    print(f"{y:2d} {row}")
