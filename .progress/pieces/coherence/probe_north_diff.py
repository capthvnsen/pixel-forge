"""Probe 8: north vs mirror(south) diff — should be ONLY the head (face stripped)."""

import sys
import tempfile
from pathlib import Path

from pixel_forge import api
from pixel_forge.animation.cycles import generate_joint_walk_cycle
from pixel_forge.domain.palette import resolve_palette
from pixel_forge.rendering.direction import project_animated_frames

sys.path.insert(0, ".progress/pieces/coherence")
from make_demo import draw_layers

tmp = Path(tempfile.mkdtemp(prefix="forge-probe8-"))
proj_root = tmp / "proj"
api.init_project(proj_root, "coherence_probe8")

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

south_m = animated["south"][0].mirror_x()
north = animated["north"][0]
a_n = north.array
a_s = south_m.array
diffs = []
for y in range(a_n.shape[0]):
    for x in range(a_n.shape[1]):
        if tuple(a_n[y, x]) != tuple(a_s[y, x]):
            diffs.append((x, y))
print(f"total diff px (north vs mirror(south), frame 0): {len(diffs)}")
# group by x to see columns; head bbox x is 9..23
xs = sorted(set(x for x, y in diffs))
print("diff x range:", min(xs), "..", max(xs))
ys = sorted(set(y for x, y in diffs))
print("diff y range:", min(ys), "..", max(ys))
# print a coarse diff map
for y in range(0, a_n.shape[0]):
    row = ""
    for x in range(a_n.shape[1]):
        row += "D" if (x, y) in diffs else "."
    if "D" in row:
        print(f"{y:2d} {row}")
