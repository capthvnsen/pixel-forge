"""Probe 6: is west walk frame N the exact mirror of east walk frame N?"""

import sys
import tempfile
from pathlib import Path

from pixel_forge import api
from pixel_forge.animation.cycles import generate_joint_walk_cycle
from pixel_forge.domain.palette import resolve_palette
from pixel_forge.rendering.direction import project_animated_frames, project_directions

sys.path.insert(0, ".progress/pieces/coherence")
from make_demo import draw_layers

tmp = Path(tempfile.mkdtemp(prefix="forge-probe6-"))
proj_root = tmp / "proj"
api.init_project(proj_root, "coherence_probe6")

front = {}
for name, img in draw_layers().items():
    p = proj_root / "layers" / f"{name}.png"
    p.parent.mkdir(parents=True, exist_ok=True)
    img.save(p)
    front[name] = p

api.import_layered(proj_root, "hero", front, timestamp="2026-08-07T00:00:00Z")
doc = api.get_asset(proj_root, "hero")
palette = resolve_palette(doc.palette)

# rest poses: west must equal mirror of east
rest = project_directions(doc, palette)
east_rest = rest["east"].composite(doc.asset.canvas)
west_rest = rest["west"].composite(doc.asset.canvas)
print("rest west == mirror(east rest):", west_rest.equals(east_rest.mirror_x()))

# walk frames: west frame i must equal mirror(east frame i)
walk = generate_joint_walk_cycle(doc, {})
animated = project_animated_frames(doc, palette, walk)
for i in range(len(walk)):
    e = animated["east"][i]
    w = animated["west"][i]
    same = w.equals(e.mirror_x())
    print(f"walk frame {i}: west == mirror(east)? {same}")
