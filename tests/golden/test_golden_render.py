"""Golden-image coverage: composed frames, mirroring, translation, palette swap, sheets, seams.

Regenerate fixtures with `UPDATE_GOLDEN=1 uv run pytest tests/golden`, then re-run without
the env var to confirm they pass before committing the PNGs under `tests/golden/fixtures/`.
"""

from __future__ import annotations

from typing import Any

from pixel_forge.animation import resolve_frames
from pixel_forge.domain.palette import ResolvedPalette, resolve_palette
from pixel_forge.rendering.canvas import Canvas
from pixel_forge.rendering.local import LocalRenderBackend, render_asset_frames
from pixel_forge.rendering.sheet import build_contact_sheet, build_seam_map, build_sprite_sheet
from pixel_forge.schemas import Palette, PaletteColor, parse_asset_doc

GoldenImage = Any  # the golden_image fixture's callable type, see tests/golden/conftest.py


def _sprite_doc() -> Any:
    return parse_asset_doc(
        {
            "schema_version": 1,
            "asset": {"id": "sprite", "type": "character", "canvas": [8, 8]},
            "palette": {
                "id": "p",
                "colors": [{"id": "red", "hex": "#c83232"}, {"id": "blue", "hex": "#3264c8"}],
            },
            "directions": ["south", "east", "west"],
            "mirror": {"west": "east"},
            "anchors": {"root": [1, 1]},
            "regions": {
                "body": {
                    "anchor": "root",
                    "layer": 0,
                    "shapes": [{"op": "rect", "color": "red", "at": [0, 0], "size": [4, 4]}],
                },
                "hat": {
                    "anchor": "root",
                    "layer": 1,
                    "shapes": [{"op": "ellipse", "color": "blue", "at": [0, -1], "size": [3, 3]}],
                },
            },
            "direction_overrides": {},
            "animations": {
                "idle": {
                    "loop": True,
                    "frames": [
                        {"duration_ms": 100, "events": [], "transforms": {}},
                        {
                            "duration_ms": 100,
                            "events": [],
                            "transforms": {"body": {"offset": [1, 0]}},
                        },
                    ],
                }
            },
            "export": {},
            "validation": {},
        }
    )


def _terrain_doc() -> Any:
    return parse_asset_doc(
        {
            "schema_version": 1,
            "asset": {"id": "terrain", "type": "terrain", "canvas": [4, 4]},
            "palette": {
                "id": "p",
                "colors": [{"id": "grass", "hex": "#3ca03c"}, {"id": "dirt", "hex": "#8a5a2a"}],
            },
            "export": {},
            "validation": {},
            "tiles": {
                "a": {
                    "size": [4, 4],
                    "anchors": {"root": [0, 0]},
                    "regions": {
                        "fill": {
                            "anchor": "root",
                            "layer": 0,
                            "shapes": [
                                {"op": "rect", "color": "grass", "at": [0, 0], "size": [4, 4]}
                            ],
                        }
                    },
                },
                "b": {
                    "size": [4, 4],
                    "anchors": {"root": [0, 0]},
                    "regions": {
                        "fill": {
                            "anchor": "root",
                            "layer": 0,
                            "shapes": [
                                {"op": "rect", "color": "dirt", "at": [0, 0], "size": [4, 4]}
                            ],
                        },
                        "mark": {
                            "anchor": "root",
                            "layer": 1,
                            "shapes": [{"op": "pixel", "color": "grass", "at": [0, 0]}],
                        },
                    },
                },
            },
        }
    )


def test_golden_composed_multi_region_frame(golden_image: GoldenImage) -> None:
    doc = _sprite_doc()
    palette = resolve_palette(doc.palette)
    frame = next(f for f in resolve_frames(doc) if f.direction == "south" and f.index == 0)
    canvas = LocalRenderBackend().render_frame(doc, frame, palette)
    golden_image("composed_frame", canvas)


def test_golden_direction_mirroring(golden_image: GoldenImage) -> None:
    doc = _sprite_doc()
    palette = resolve_palette(doc.palette)
    frame = next(f for f in resolve_frames(doc) if f.direction == "west" and f.index == 0)
    canvas = LocalRenderBackend().render_frame(doc, frame, palette)
    golden_image("mirrored_frame", canvas)


def test_golden_region_translation(golden_image: GoldenImage) -> None:
    doc = _sprite_doc()
    palette = resolve_palette(doc.palette)
    frame = next(f for f in resolve_frames(doc) if f.direction == "south" and f.index == 1)
    canvas = LocalRenderBackend().render_frame(doc, frame, palette)
    golden_image("translated_frame", canvas)


def test_golden_palette_replacement(golden_image: GoldenImage) -> None:
    doc = _sprite_doc()
    swapped = ResolvedPalette(
        palette=Palette(
            id="swapped",
            colors=[
                PaletteColor(id="red", hex="#20c020"),
                PaletteColor(id="blue", hex="#c0c020"),
            ],
        )
    )
    frame = next(f for f in resolve_frames(doc) if f.direction == "south" and f.index == 0)
    canvas = LocalRenderBackend().render_frame(doc, frame, swapped)
    golden_image("palette_replaced_frame", canvas)


def test_golden_sprite_sheet_layout(golden_image: GoldenImage) -> None:
    doc = _sprite_doc()
    rendered = render_asset_frames(doc)
    pairs = [
        (frame, rendered[(frame.animation, frame.direction, frame.index)])
        for frame in resolve_frames(doc)
    ]
    sheet = build_sprite_sheet(pairs, doc.asset.canvas)
    golden_image("sprite_sheet", sheet.image)


def test_golden_contact_sheet_layout(golden_image: GoldenImage) -> None:
    doc = _sprite_doc()
    rendered = render_asset_frames(doc)
    pairs = [
        (frame, rendered[(frame.animation, frame.direction, frame.index)])
        for frame in resolve_frames(doc)
    ]
    sheet = build_sprite_sheet(pairs, doc.asset.canvas)
    contact = build_contact_sheet(sheet)
    golden_image("contact_sheet", contact)


def test_golden_seam_map(golden_image: GoldenImage) -> None:
    doc = _terrain_doc()
    palette = resolve_palette(doc.palette)
    backend = LocalRenderBackend()
    tiles: dict[str, Canvas] = {
        tile_id: backend.render_tile(doc, tile_id, palette) for tile_id in sorted(doc.tiles)
    }
    seam_canvas = build_seam_map(tiles, [["a", "b"], ["b", "a"]])
    golden_image("seam_map", seam_canvas)
