"""Probe 9: hole probe — no transparent holes (4 opaque neighbours) in head band."""

import sys
import tempfile
from pathlib import Path

from pixel_forge import api
from pixel_forge.domain.palette import resolve_palette
from pixel_forge.rendering.direction import project_directions

sys.path.insert(0, ".progress/pieces/coherence")
from make_demo import draw_layers

tmp = Path(tempfile.mkdtemp(prefix="forge-probe9-"))
proj_root = tmp / "proj"
api.init_project(proj_root, "coherence_probe9")

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


def holes(c, band=(0, 20)):
    a = c.array
    out = []
    for y in range(band[0], band[1]):
        for x in range(a.shape[1]):
            if tuple(a[y, x])[3] != 0:
                continue
            nbrs = 0
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= ny < a.shape[0] and 0 <= nx < a.shape[1] and tuple(a[ny, nx])[3] > 0:
                    nbrs += 1
            if nbrs == 4:
                out.append((x, y))
    return out


all_holes = {}
for d in ("north", "north_east", "east", "south_east", "south", "south_west", "west", "north_west"):
    c = views[d].composite(doc.asset.canvas)
    h = holes(c)
    all_holes[d] = h
    print(f"{d:12s} holes in head band: {h}")

print("TOTAL holes:", sum(len(v) for v in all_holes.values()))
