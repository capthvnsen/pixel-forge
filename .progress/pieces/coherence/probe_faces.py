"""Probe: pixel-verify critic claims about the coherence demo projections."""

import sys
import tempfile
from collections import Counter
from pathlib import Path

from pixel_forge import api
from pixel_forge.domain.palette import resolve_palette
from pixel_forge.rendering.direction import (
    _render_region_canvases,
    project_directions,
)

sys.path.insert(0, ".progress/pieces/coherence")
from make_demo import draw_layers

tmp = Path(tempfile.mkdtemp(prefix="forge-probe-"))
proj_root = tmp / "proj"
api.init_project(proj_root, "coherence_probe")

front = {}
for name, img in draw_layers().items():
    p = proj_root / "layers" / f"{name}.png"
    p.parent.mkdir(parents=True, exist_ok=True)
    img.save(p)
    front[name] = p

result = api.import_layered(proj_root, "hero", front, timestamp="2026-08-07T00:00:00Z")
doc = api.get_asset(proj_root, "hero")
palette = resolve_palette(doc.palette)

print("PALETTE ids:", list(palette.ids))
print("PALETTE colors:", [(c.id, c.hex) for c in palette.palette.colors])
print("REGIONS:", list(doc.regions.keys()))
for name, r in doc.regions.items():
    print(f"  {name}: anchor={r.anchor} layer={r.layer} mirror_safe={r.mirror_safe}")
print("ANCHORS:", doc.anchors)

canvases = _render_region_canvases(doc, palette)
head = canvases["head"]
print("HEAD bbox:", head.bbox())
arr = head.array
colors = Counter()
for y in range(arr.shape[0]):
    for x in range(arr.shape[1]):
        px = tuple(arr[y, x])
        if px[3] > 0:
            colors[px] += 1
print("HEAD color counts:", dict(colors))


def is_opaque(y, x):
    if y < 0 or x < 0 or y >= arr.shape[0] or x >= arr.shape[1]:
        return False
    return arr[y, x][3] > 0


darkest = min(colors, key=lambda c: (c[0] + c[1] + c[2]) / 3)
print("darkest color:", darkest)
interior_ink = []
for y in range(arr.shape[0]):
    for x in range(arr.shape[1]):
        if tuple(arr[y, x]) == darkest and all(
            is_opaque(y + dy, x + dx) for dy, dx in [(1, 0), (-1, 0), (0, 1), (0, -1)]
        ):
            interior_ink.append((x, y))
print("interior ink pixels in head:", interior_ink)


def ascii_canvas(c, threshold=0):
    """Render an ASCII map: . = transparent, else the pixel's alpha or char."""
    a = c.array
    out = []
    for y in range(a.shape[0]):
        row = ""
        for x in range(a.shape[1]):
            px = tuple(a[y, x])
            if px[3] == 0:
                row += "."
            else:
                row += "#"
        out.append(row)
    return "\n".join(out)


views = project_directions(doc, palette)
for d in ("north", "east", "south_east"):
    v = views[d]
    comp = v.composite(doc.asset.canvas)
    print(f"\n=== {d} (composite, {comp.width}x{comp.height}) ===")
    print(ascii_canvas(comp))
    # which region canvases contribute opaque pixels around the head area (y 0..20)?
    head_regions = [r for r in v.regions if r.name in ("head", "hair")]
    for r in head_regions:
        bbox = r.canvas.bbox()
        print(f"  region {r.name}: layer={r.layer} bbox={bbox}")
