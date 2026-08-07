"""Probe 4: walk frames at max stride — measure leg separation & X-blob."""

import sys
import tempfile
from pathlib import Path

from pixel_forge import api
from pixel_forge.animation.cycles import generate_joint_walk_cycle
from pixel_forge.domain.palette import resolve_palette
from pixel_forge.rendering.direction import project_animated_frames

sys.path.insert(0, ".progress/pieces/coherence")
from make_demo import draw_layers

tmp = Path(tempfile.mkdtemp(prefix="forge-probe4-"))
proj_root = tmp / "proj"
api.init_project(proj_root, "coherence_probe4")

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
# print the max leg angles used per frame
for i, f in enumerate(walk):
    leg_angles = [
        t.rotate.angle_deg
        for name, t in f.transforms.items()
        if "leg" in name and t.rotate is not None
    ]
    print(f"frame {i}: leg angles {leg_angles}")

animated = project_animated_frames(doc, palette, walk)

# Frame with the largest swing: find it
largest = max(range(len(walk)), key=lambda i: abs(walk[i].transforms["leg_left"].rotate.angle_deg))
print("largest swing frame:", largest, walk[largest].transforms["leg_left"].rotate.angle_deg)

# Render south direction, max stride frame, ASCII in leg area (y 30..50)
frame = animated["south"][largest]
a = frame.array
print(f"\n=== south frame {largest} (max stride) ===")
for y in range(28, 51):
    row = ""
    for x in range(a.shape[1]):
        px = tuple(a[y, x])
        row += "#" if px[3] > 0 else "."
    print(f"{y:2d} {row}")
