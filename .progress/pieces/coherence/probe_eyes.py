"""Probe 2: where do the ink (eye) pixels land in each direction view?"""

import sys
import tempfile
from pathlib import Path

from pixel_forge import api
from pixel_forge.domain.palette import resolve_palette
from pixel_forge.rendering.direction import project_directions

sys.path.insert(0, ".progress/pieces/coherence")
from make_demo import draw_layers

tmp = Path(tempfile.mkdtemp(prefix="forge-probe2-"))
proj_root = tmp / "proj"
api.init_project(proj_root, "coherence_probe2")

front = {}
for name, img in draw_layers().items():
    p = proj_root / "layers" / f"{name}.png"
    p.parent.mkdir(parents=True, exist_ok=True)
    img.save(p)
    front[name] = p

api.import_layered(proj_root, "hero", front, timestamp="2026-08-07T00:00:00Z")
doc = api.get_asset(proj_root, "hero")
palette = resolve_palette(doc.palette)
views = project_directions(doc, palette)

INK = (26, 22, 18, 255)


def eye_pixels(c):
    a = c.array
    pts = []
    for y in range(a.shape[0]):
        for x in range(a.shape[1]):
            if tuple(a[y, x]) == INK:
                pts.append((x, y))
    return pts


for d in ("north", "north_east", "east", "south_east", "south"):
    c = views[d].composite(doc.asset.canvas)
    ink = eye_pixels(c)
    band = [p for p in ink if 2 <= p[1] <= 16]
    print(f"{d:12s} head-band ink px: {band}  ({len(band)})")
