"""Round-7 verification probe: ramp completeness + shading on the fixed demo art."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".progress/pieces/coherence")
from make_demo import draw_layers

from pixel_forge import api
from pixel_forge.domain.palette import resolve_palette
from pixel_forge.rendering.direction import _infer_ramps, project_directions

tmp = Path(tempfile.mkdtemp(prefix="r7-probe-"))
root = tmp / "proj"
api.init_project(root, "r7")
front = {}
for name, img in draw_layers().items():
    p = root / "layers" / f"{name}.png"
    p.parent.mkdir(parents=True, exist_ok=True)
    img.save(p)
    front[name] = p
api.import_layered(root, "hero", front, timestamp="2026-08-07T00:00:00Z")
doc = api.get_asset(root, "hero")
palette = resolve_palette(doc.palette)
ramps = _infer_ramps(palette)
print("families:", [[palette.rgba(c) for c in fam] for fam in ramps.families])

# pants ramp complete?
pants_lo = palette.rgba("c0a") if any(c.id == "c0a" for c in doc.palette.colors) else None
pants_ids = {c.hex for c in doc.palette.colors}
print("pants_lo (#3c324e) in palette:", "#3c324e" in pants_ids)
belt_dark = (52, 38, 30, 255)
in_family = any(belt_dark in [palette.rgba(c) for c in fam] for fam in ramps.families)
print("belt_dark excluded from ramps:", not in_family)

views = project_directions(doc, palette)
W = doc.asset.canvas[0]

SHIRT_HI = (118, 168, 224, 255)
SHIRT_LO = (58, 94, 158, 255)
HAIR_HI = (210, 160, 74, 255)
PANTS_LO = (60, 50, 78, 255)


def band_colors(direction, y0, y1):
    c = views[direction].composite(doc.asset.canvas)
    out = set()
    for y in range(y0, y1):
        for x in range(c.width):
            rgba = tuple(c.array[y, x])
            if rgba[3] > 0:
                out.add(rgba)
    return out


# east torso band (find torso y-range via the region canvas bbox)
torso_east = next(r for r in views["east"].regions if r.name == "torso")
a = torso_east.canvas.array
hi = [x for y in range(a.shape[0]) for x in range(a.shape[1]) if tuple(a[y, x]) == SHIRT_HI]
lo = [x for y in range(a.shape[0]) for x in range(a.shape[1]) if tuple(a[y, x]) == SHIRT_LO]
print("east torso shirt_hi xs:", sorted(set(hi))[:6], "shirt_lo xs:", sorted(set(lo))[:6])
print("east chest light correct (hi right of lo):", bool(hi and lo and min(hi) > max(lo)))

# belt colour stays dark in east (not flipped to hair_hi)
east_cols = set()
for r in views["east"].regions:
    east_cols |= set(r.canvas.colors())
print("east contains belt_dark:", belt_dark in east_cols)
hair_hi_rgba = HAIR_HI
# the belt band is rows 33-35 world; check composite
c = views["east"].composite(doc.asset.canvas)
belt_band = set()
for y in range(c.height):
    for x in range(c.width):
        rgba = tuple(c.array[y, x])
        if rgba in (belt_dark, hair_hi_rgba):
            belt_band.add((rgba, x, y))
print("east belt_band colours:", sorted({rgba for rgba, _, _ in belt_band}))

# diagonal far-leg darkening (pants_lo > 0 in the composite legs band)
for d in ("south_east", "south_west", "north_east", "north_west"):
    c = views[d].composite(doc.asset.canvas)
    lo_count = sum(
        1 for y in range(c.height) for x in range(c.width) if tuple(c.array[y, x]) == PANTS_LO
    )
    print(f"{d}: pants_lo pixels in composite: {lo_count}")

# side legs readable: east leg region has a pants colour (not ink-only)
leg_east = next(r for r in views["east"].regions if r.name == "leg_right")
print("east leg_right colours:", sorted(leg_east.canvas.colors()))
