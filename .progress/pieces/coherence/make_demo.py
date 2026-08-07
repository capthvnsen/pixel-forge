"""Coherence demo: one layered front drawing -> 8 directions -> animated sheet.

The product spine (HANDOFF.md piece #5 / coherence pass): the user uploads a
single layered front-view drawing; the engine imports it, projects all 8
directions, runs the joint-pivot walk cycle through every view, and packs the
result into a sprite sheet — all deterministic, palette-disciplined, and
byte-exact to the source art until polish is opted in.

This script draws a real (warden-quality) layered character — proper
proportions, ink outline, upper-left-light shading ramps, three-pixel limbs —
then drives the actual pipeline:

    layered PNGs -> api.import_layered (spec, byte-exact round-trip)
    -> project_directions (8 views, rest pose)
    -> generate_joint_walk_cycle + project_animated_frames (walk through 8 dirs)
    -> sprite sheet PNGs for the critic

Output (all under .progress/pieces/coherence/):
    front.png                    the composited source front view (x4)
    import_roundtrip.png         engine-rendered idle/south/0 vs source (x4)
    walk_sheet.png               8 directions x 8 walk frames (x4)
    rest_sheet.png               8 directional rest poses (x4)
    flow.txt                     sha256s proving determinism + round-trip

Run: uv run python .progress/pieces/coherence/make_demo.py
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from PIL import Image

from pixel_forge import api
from pixel_forge.animation.cycles import generate_joint_walk_cycle
from pixel_forge.domain.palette import resolve_palette
from pixel_forge.rendering.canvas import Canvas
from pixel_forge.rendering.direction import project_animated_frames, project_directions

OUT_DIR = Path(__file__).parent

# --- layered source art (the "user upload") -----------------------------------

CANVAS = (48, 56)
X0, Y0 = 4, 0  # the drawing's own origin; the importer derives/keeps coordinates

PALETTE = {
    "ink": (26, 22, 18, 255),
    "skin_hi": (244, 214, 182, 255),
    "skin_mid": (224, 182, 148, 255),
    "skin_lo": (196, 150, 116, 255),
    "hair_hi": (210, 160, 74, 255),
    "hair_mid": (176, 128, 46, 255),
    "hair_lo": (140, 98, 34, 255),
    "shirt_hi": (118, 168, 224, 255),
    "shirt_mid": (84, 130, 196, 255),
    "shirt_lo": (58, 94, 158, 255),
    "pants_hi": (108, 96, 130, 255),
    "pants_mid": (84, 72, 104, 255),
    "pants_lo": (60, 50, 78, 255),
    "boot": (64, 54, 66, 255),
    "belt_dark": (52, 38, 30, 255),
    "shadow": (24, 24, 32, 255),
}


def _blank() -> Image.Image:
    return Image.new("RGBA", CANVAS, (0, 0, 0, 0))


def _px(img: Image.Image, x: int, y: int, color: str) -> None:
    img.putpixel((x, y), PALETTE[color])


def _rect(img: Image.Image, x0: int, y0: int, x1: int, y1: int, color: str) -> None:
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            _px(img, x, y, color)


def _outline_rect(img: Image.Image, x0: int, y0: int, x1: int, y1: int, fill: str) -> None:
    _rect(img, x0, y0, x1, y1, "ink")
    _rect(img, x0 + 1, y0 + 1, x1 - 1, y1 - 1, fill)


def draw_layers() -> dict[str, Image.Image]:
    """A 48x48 humanoid: head with hair + face, torso with belt, two arms with
    hands, two legs with boots, ground shadow. Upper-left light throughout."""
    layers: dict[str, Image.Image] = {
        name: _blank()
        for name in (
            "torso",
            "head",
            "arm_left",
            "arm_right",
            "leg_left",
            "leg_right",
            "hair",
            "shadow",
        )
    }

    # --- legs (4px wide so the side squash keeps a readable fill, with boot,
    # knee break, and a pants_lo boot-shadow so the FULL pants ramp survives
    # import — the round-6 critic: a 2-step ramp silently no-ops the far-limb
    # depth shading) ---
    for side, x in (("leg_left", 18), ("leg_right", 27)):
        img = layers[side]
        _outline_rect(img, x, 36, x + 3, 45, "pants_mid")
        _rect(img, x + 1, 40, x + 2, 41, "pants_hi")  # knee catch
        _outline_rect(img, x, 45, x + 3, 48, "boot")
        _rect(img, x + 1, 45, x + 2, 46, "boot")  # boot top light
        _rect(img, x + 1, 47, x + 2, 48, "pants_lo")  # boot sole shadow

    # --- torso (with belt + chest light) ---
    torso = layers["torso"]
    _outline_rect(torso, 14, 16, 33, 35, "shirt_mid")
    _rect(torso, 15, 17, 16, 33, "shirt_hi")  # left-edge light
    _rect(torso, 31, 17, 32, 33, "shirt_lo")  # right-edge shade (2px: survives the side squash)
    # Belt is a NEAR-BLACK brown (CIE L* < 20): the ramp inference excludes
    # near-blacks, so the belt can never join the hair family and get flipped
    # into hair_hi on the side views (round-6 critic's cross-material bug).
    _outline_rect(torso, 15, 33, 32, 35, "belt_dark")

    # --- arms (3px wide, skin hand, shirt sleeve) ---
    # Hands sit 1px farther from the canvas edges (x=12/x=33) so the walk's
    # geometry arm clamp (asin((edge_distance - 1px)/arm_length)) unlocks a
    # visible 2-3px counter-swing instead of capping at 1px shimmer (round-9
    # critic: hand edge clearance 2px -> swing 4.096°; 3-4px -> ~8-12°).
    for side, x in (("arm_left", 12), ("arm_right", 33)):
        img = layers[side]
        _outline_rect(img, x, 17, x + 2, 28, "shirt_mid")
        _rect(img, x, 18, x, 27, "shirt_hi")
        _outline_rect(img, x, 28, x + 2, 31, "skin_mid")  # hand
        _rect(img, x, 28, x, 29, "skin_hi")

    # --- head + hair + face ---
    head = layers["head"]
    _outline_rect(head, 17, 4, 30, 17, "skin_mid")
    _rect(head, 18, 5, 19, 15, "skin_hi")  # cheek light
    _rect(head, 29, 5, 29, 15, "skin_lo")
    _px(head, 21, 10, "ink")  # eye (far)
    _px(head, 25, 10, "ink")  # eye (near)

    hair = layers["hair"]
    _rect(hair, 16, 3, 31, 6, "hair_mid")
    _rect(hair, 17, 2, 30, 2, "hair_hi")
    _px(hair, 18, 7, "hair_mid")
    _px(hair, 31, 3, "hair_lo")  # right-edge hair shadow (completes the ramp)

    # --- ground shadow ---
    shadow = layers["shadow"]
    for i, x0 in enumerate((8, 9, 10)):
        _rect(shadow, x0 + i, 51, 38 - i, 51, "shadow")
    _rect(shadow, 11, 52, 36, 52, "shadow")

    return layers


# --- helpers -------------------------------------------------------------------


def _stage_layers(tmp: Path) -> dict[str, Path]:
    tmp.mkdir(parents=True, exist_ok=True)
    layers = draw_layers()
    out: dict[str, Path] = {}
    for name, img in layers.items():
        path = tmp / f"{name}.png"
        img.save(path)
        out[name] = path
    return out


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _x4(canvas: Canvas) -> Image.Image:
    return canvas.scale(4).to_image()


def _content_bbox(composite: Canvas) -> tuple[int, int, int, int] | None:
    return composite.bbox()


def _tight_panel_size(composites: list[Canvas], pad: int = 2) -> tuple[int, int]:
    """A single uniform panel size (content bbox + pad) that fits every
    composite — the round-8 critic: source-canvas cells wasted ~35% dead space
    and made grid-sliced sprite offsets direction-dependent."""
    max_w = max_h = 0
    for c in composites:
        bbox = _content_bbox(c)
        if bbox is None:
            continue
        max_w = max(max_w, bbox[2] - bbox[0] + 1)
        max_h = max(max_h, bbox[3] - bbox[1] + 1)
    return max_w + 2 * pad, max_h + 2 * pad


def _blit_centered(
    sheet: Canvas, composite: Canvas, panel: tuple[int, int], pad: int, col: int, row: int
) -> None:
    """Blit `composite` into the cell at (col, row), centering its content in
    the uniform panel so every view's cell-relative offset is identical."""
    panel_w, panel_h = panel
    bbox = _content_bbox(composite)
    if bbox is None:
        return
    x0, y0, x1, y1 = bbox
    cw, ch = x1 - x0 + 1, y1 - y0 + 1
    ox = pad + (panel_w - cw) // 2 - x0
    oy = pad + (panel_h - ch) // 2 - y0
    sheet.blit(
        composite,
        (
            col * (panel_w + PANEL_GAP) + PANEL_GAP + ox,
            row * (panel_h + PANEL_GAP) + PANEL_GAP + oy,
        ),
    )


PANEL_GAP = 2


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="forge-coherence-"))

    # 0. project scaffold (the import writes into a real project)
    proj_root = tmp / "proj"
    api.init_project(proj_root, "coherence_demo")

    # Stage the layer PNGs inside the project (import_layered resolves layer
    # paths through safe_join against the project root).
    front = _stage_layers(proj_root / "layers")

    # 1. import (the spec is the intermediate representation)
    result = api.import_layered(proj_root, "hero", front, timestamp="2026-08-07T00:00:00Z")
    root = proj_root
    doc = api.get_asset(root, "hero")
    assert doc.asset.type == "character"
    palette = resolve_palette(doc.palette)

    # 2. byte-exact round-trip proof
    from pixel_forge.rendering import render_asset_frames

    rendered = render_asset_frames(doc, art_direction=None)[("idle", "south", 0)]
    rendered.scale(4).save_png(OUT_DIR / "import_roundtrip.png")
    rendered.scale(4).to_image().save(OUT_DIR / "front.png")

    # 3. rest poses: all 8 directions
    rest = project_directions(doc, palette)
    rest_composites = [rest[d].composite(doc.asset.canvas) for d in rest]
    panel = _tight_panel_size(rest_composites)
    sheet = Canvas(4 * panel[0] + 5 * PANEL_GAP, 2 * panel[1] + 3 * PANEL_GAP)
    layout = (
        ("north_west", "north", "north_east", "south_east"),
        ("west", "east", "south_west", "south"),
    )
    for row, dirs in enumerate(layout):
        for col, d in enumerate(dirs):
            _blit_centered(sheet, rest[d].composite(doc.asset.canvas), panel, PANEL_GAP, col, row)
    sheet.scale(4).save_png(OUT_DIR / "rest_sheet.png")

    # 4. joint walk through all 8 directions
    walk = generate_joint_walk_cycle(doc, {})
    animated = project_animated_frames(doc, palette, walk)
    walk_composites = [f for frames in animated.values() for f in frames]
    panel_w = _tight_panel_size(walk_composites)
    sheet_w = Canvas(8 * panel_w[0] + 9 * PANEL_GAP, 8 * panel_w[1] + 9 * PANEL_GAP)
    for row, direction in enumerate(
        ("north_west", "north", "north_east", "east", "west", "south_east", "south_west", "south")
    ):
        for col, frame_canvas in enumerate(animated[direction]):
            _blit_centered(sheet_w, frame_canvas, panel_w, PANEL_GAP, col, row)
    sheet_w.scale(4).save_png(OUT_DIR / "walk_sheet.png")

    # 5. determinism proof
    rendered2 = render_asset_frames(doc, art_direction=None)[("idle", "south", 0)]
    walk2 = project_animated_frames(doc, palette, generate_joint_walk_cycle(doc, {}))
    walk_ok = all(
        all(a.equals(b) for a, b in zip(animated[d], walk2[d], strict=True)) for d in animated
    )
    flow = OUT_DIR / "flow.txt"
    flow.write_text(
        "coherence demo: layered front -> import -> 8 dirs -> joint walk\n"
        f"import  canvas: {result.canvas[0]}x{result.canvas[1]}, palette {result.palette_size}\n"
        f"roundtrip sha256: {_sha(OUT_DIR / 'import_roundtrip.png')}\n"
        f"front    sha256: {_sha(OUT_DIR / 'front.png')}\n"
        f"rest     sha256: {_sha(OUT_DIR / 'rest_sheet.png')}\n"
        f"walk     sha256: {_sha(OUT_DIR / 'walk_sheet.png')}\n"
        f"determinism: render {rendered.equals(rendered2)}, "
        f"walk {walk_ok}\n"
        f"validation blocking: "
        f"{api.validate_asset(root, 'hero').blocking}\n"
    )
    print(flow.read_text())


if __name__ == "__main__":
    main()
