"""Pivot-articulation contact sheet for the critic.

Builds a 32x32 front-view character (head/torso/arms/legs as anchored regions,
arms anchored at shoulders, legs at hips) and renders it through the real backend
path (`render_asset_frames` -> `LocalRenderBackend` -> `plan_layers`/`composite`),
one animation frame per swing angle. Panels left to right:

    angle = -90, -60, -30, 0, +30, +60, +90 degrees

Per panel: arm_left rotates +angle about the left shoulder, arm_right -angle
(mirrored swing), leg_left -angle/2 and leg_right +angle/2 about the hips
(walk-opposition phasing). All pivots are the default RotateSpec pivot — the
region's own anchor — which is the joint contract this piece ships.

Output: pivot_contact_sheet.png (7 panels, 2px gutters, nearest-neighbour x4).
Prints the sheet's sha256; run twice to eyeball determinism.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from pixel_forge.rendering.canvas import Canvas
from pixel_forge.rendering.local import render_asset_frames
from pixel_forge.schemas import parse_asset_doc

ANGLES = [-90.0, -60.0, -30.0, 0.0, 30.0, 60.0, 90.0]
PANEL = 32
GUTTER = 2
OUT_DIR = Path(__file__).parent


def _character_doc() -> Any:
    frames = []
    for angle in ANGLES:
        frames.append(
            {
                "duration_ms": 100,
                "events": [],
                "transforms": {
                    "arm_left": {"rotate": {"angle_deg": angle}},
                    "arm_right": {"rotate": {"angle_deg": -angle}},
                    "leg_left": {"rotate": {"angle_deg": -angle / 2}},
                    "leg_right": {"rotate": {"angle_deg": angle / 2}},
                },
            }
        )
    return parse_asset_doc(
        {
            "schema_version": 1,
            "asset": {"id": "pivot_dummy", "type": "character", "canvas": [32, 32]},
            "palette": {
                "id": "p",
                "colors": [
                    {"id": "skin", "hex": "#e8b58c"},
                    {"id": "shirt", "hex": "#3a6ea5"},
                    {"id": "pants", "hex": "#4a4a5a"},
                ],
            },
            "directions": ["south"],
            "anchors": {
                "neck": [16, 8],
                "root": [16, 20],
                "shoulder_left": [11, 10],
                "shoulder_right": [20, 10],
                "hip_left": [14, 20],
                "hip_right": [18, 20],
            },
            "regions": {
                "leg_left": {
                    "anchor": "hip_left",
                    "layer": 0,
                    "shapes": [{"op": "rect", "color": "pants", "at": [-1, 0], "size": [2, 8]}],
                },
                "leg_right": {
                    "anchor": "hip_right",
                    "layer": 0,
                    "shapes": [{"op": "rect", "color": "pants", "at": [-1, 0], "size": [2, 8]}],
                },
                "torso": {
                    "anchor": "root",
                    "layer": 1,
                    "shapes": [{"op": "rect", "color": "shirt", "at": [-4, -11], "size": [8, 11]}],
                },
                "arm_left": {
                    "anchor": "shoulder_left",
                    "layer": 2,
                    "shapes": [{"op": "rect", "color": "skin", "at": [-1, 0], "size": [2, 7]}],
                },
                "arm_right": {
                    "anchor": "shoulder_right",
                    "layer": 2,
                    "shapes": [{"op": "rect", "color": "skin", "at": [-1, 0], "size": [2, 7]}],
                },
                "head": {
                    "anchor": "neck",
                    "layer": 3,
                    "shapes": [{"op": "ellipse", "color": "skin", "at": [-3, -7], "size": [7, 7]}],
                },
            },
            "animations": {"swing": {"loop": False, "frames": frames}},
            "export": {},
            "validation": {},
        }
    )


def main() -> None:
    frames = render_asset_frames(_character_doc())
    width = len(ANGLES) * PANEL + (len(ANGLES) + 1) * GUTTER
    height = PANEL + 2 * GUTTER
    sheet = Canvas(width, height)
    sheet.draw_rect((0, 0), (width, height), (24, 24, 32, 255), fill=True)
    for i in range(len(ANGLES)):
        panel = frames[("swing", "south", i)]
        sheet.blit(panel, (GUTTER + i * (PANEL + GUTTER), GUTTER))
    out = OUT_DIR / "pivot_contact_sheet.png"
    sheet.scale(4).save_png(out)
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"wrote {out} ({sheet.width * 4}x{sheet.height * 4}) sha256={digest}")


if __name__ == "__main__":
    main()
