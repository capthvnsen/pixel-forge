"""Probe 3: color-level detail of the east (side) view — where do arms/hands land?"""

import sys
import tempfile
from pathlib import Path

from pixel_forge import api
from pixel_forge.domain.palette import resolve_palette
from pixel_forge.rendering.direction import project_directions

sys.path.insert(0, ".progress/pieces/coherence")
from make_demo import draw_layers

tmp = Path(tempfile.mkdtemp(prefix="forge-probe3-"))
proj_root = tmp / "proj"
api.init_project(proj_root, "coherence_probe3")

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

# color-keyed ASCII for the east view (rows 0..33 = head + torso + arms)
COLOR_CHARS = {
    (244, 214, 182, 255): "H",  # skin_hi
    (224, 182, 148, 255): "h",  # skin_mid
    (196, 150, 116, 255): "l",  # skin_lo
    (118, 168, 224, 255): "B",  # shirt_hi
    (84, 130, 196, 255): "b",  # shirt_mid
    (58, 94, 158, 255): "d",  # shirt_lo
    (26, 22, 18, 255): "#",  # ink
    (210, 160, 74, 255): "Y",  # hair_hi
    (176, 128, 46, 255): "y",  # hair_mid
    (150, 118, 64, 255): "=",  # belt
    (108, 96, 130, 255): "P",  # pants_hi
    (84, 72, 104, 255): "p",  # pants_mid
    (60, 50, 78, 255): "q",  # pants_lo
    (64, 54, 66, 255): "o",  # boot
}


def ascii_color(c, max_y=None):
    a = c.array
    out = []
    for y in range(a.shape[0]):
        if max_y is not None and y > max_y:
            break
        row = ""
        for x in range(a.shape[1]):
            px = tuple(a[y, x])
            if px[3] == 0:
                row += "."
            else:
                row += COLOR_CHARS.get(px, "?")
        out.append(row)
    return "\n".join(out)


east = views["east"].composite(doc.asset.canvas)
print("=== EAST view (color, rows 0..33) ===")
print(ascii_color(east, max_y=33))

print("\n=== EAST view (color, rows 34..50) ===")
print(ascii_color(east, max_y=50)[24 * 0 + 0 :])  # noop slice to keep output small
