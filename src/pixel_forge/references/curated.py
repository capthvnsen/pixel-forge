"""Curated professional palettes (the reference quality bar, in palette form).

Each palette is a dict ready for `Palette.model_validate(...)`: one 3-tone ramp
per material (`{material}_shadow/_mid/_light`, sharing a `ramp` id, with
shadow/light `role` tags), 1-2 accent colours placed with margin, and a single
hue-tinted charcoal `outline` — never pure black.

The hex values were generated with `domain.palette.build_ramp` /
`domain.palette.derive_outline` from hand-picked material base colours, so the
library literally encodes the discipline this package enforces elsewhere:
hue-preserving value steps (shadow slightly cool, highlight slightly warm),
spaced at even CIE L* intervals so a mid-lightness base lands its light tone at
L* 60-75, and dark-charcoal outlines tinted by the palette's darkest material.
Palette totals land in the professional 16-32 colour range.

Usage::

    from pixel_forge.references.curated import load_curated_palette
    palette = load_curated_palette("rpg_fantasy")
"""

from __future__ import annotations

from typing import Any

from pixel_forge.errors import PaletteError
from pixel_forge.schemas.palette import Palette

CURATED_PALETTES: dict[str, dict[str, Any]] = {
    "rpg_fantasy": {
        "id": "rpg_fantasy",
        "colors": [
            # grass — mid green with a cool shadow and a warm sunlit top
            {"id": "grass_shadow", "hex": "#315222", "ramp": "grass", "role": "shadow"},
            {"id": "grass_mid", "hex": "#5d8f3c", "ramp": "grass"},
            {"id": "grass_light", "hex": "#a5ca80", "ramp": "grass", "role": "light"},
            # earth
            {"id": "earth_shadow", "hex": "#3e2d17", "ramp": "earth", "role": "shadow"},
            {"id": "earth_mid", "hex": "#8a5a33", "ramp": "earth"},
            {"id": "earth_light", "hex": "#cc9374", "ramp": "earth", "role": "light"},
            # stone
            {"id": "stone_shadow", "hex": "#454647", "ramp": "stone", "role": "shadow"},
            {"id": "stone_mid", "hex": "#7d7d80", "ramp": "stone"},
            {"id": "stone_light", "hex": "#bababb", "ramp": "stone", "role": "light"},
            # leather
            {"id": "leather_shadow", "hex": "#281e11", "ramp": "leather", "role": "shadow"},
            {"id": "leather_mid", "hex": "#6e4a2f", "ramp": "leather"},
            {"id": "leather_light", "hex": "#ba7d5e", "ramp": "leather", "role": "light"},
            # metal
            {"id": "metal_shadow", "hex": "#546169", "ramp": "metal", "role": "shadow"},
            {"id": "metal_mid", "hex": "#8f9aa5", "ramp": "metal"},
            {"id": "metal_light", "hex": "#d1d5da", "ramp": "metal", "role": "light"},
            # water
            {"id": "water_shadow", "hex": "#1e3a50", "ramp": "water", "role": "shadow"},
            {"id": "water_mid", "hex": "#3f6fa8", "ramp": "water"},
            {"id": "water_light", "hex": "#91aad6", "ramp": "water", "role": "light"},
            # wood
            {"id": "wood_shadow", "hex": "#322514", "ramp": "wood", "role": "shadow"},
            {"id": "wood_mid", "hex": "#7a5230", "ramp": "wood"},
            {"id": "wood_light", "hex": "#c38765", "ramp": "wood", "role": "light"},
            # accents — placed with margin, not part of any material ramp
            {"id": "accent_red", "hex": "#c0392b", "ramp": "accent"},
            {"id": "accent_gold", "hex": "#c9a227", "ramp": "accent"},
            # outline — dark charcoal tinted by the palette's darkest material
            {"id": "outline", "hex": "#14100f", "role": "outline"},
        ],
    },
    "dungeon": {
        "id": "dungeon",
        "colors": [
            {"id": "stone_shadow", "hex": "#36373a", "ramp": "stone", "role": "shadow"},
            {"id": "stone_mid", "hex": "#6d6d75", "ramp": "stone"},
            {"id": "stone_light", "hex": "#a9a9ae", "ramp": "stone", "role": "light"},
            {"id": "iron_shadow", "hex": "#202528", "ramp": "iron", "role": "shadow"},
            {"id": "iron_mid", "hex": "#4f5863", "ramp": "iron"},
            {"id": "iron_light", "hex": "#8991a1", "ramp": "iron", "role": "light"},
            {"id": "wood_shadow", "hex": "#1b150c", "ramp": "wood", "role": "shadow"},
            {"id": "wood_mid", "hex": "#5b4228", "ramp": "wood"},
            {"id": "wood_light", "hex": "#ab724b", "ramp": "wood", "role": "light"},
            {"id": "bone_shadow", "hex": "#7a7550", "ramp": "bone", "role": "shadow"},
            {"id": "bone_mid", "hex": "#b8b093", "ramp": "bone"},
            {"id": "bone_light", "hex": "#dad4c6", "ramp": "bone", "role": "light"},
            {"id": "moss_shadow", "hex": "#20321b", "ramp": "moss", "role": "shadow"},
            {"id": "moss_mid", "hex": "#4a6b3a", "ramp": "moss"},
            {"id": "moss_light", "hex": "#7fa95f", "ramp": "moss", "role": "light"},
            {"id": "flame_shadow", "hex": "#6b4211", "ramp": "flame", "role": "shadow"},
            {"id": "flame_mid", "hex": "#c96a1f", "ramp": "flame"},
            {"id": "flame_light", "hex": "#efb495", "ramp": "flame", "role": "light"},
            {"id": "gold", "hex": "#c9a227", "ramp": "accent"},
            {"id": "outline", "hex": "#110f0d", "role": "outline"},
        ],
    },
    "forest": {
        "id": "forest",
        "colors": [
            {"id": "leaf_shadow", "hex": "#243f18", "ramp": "leaf", "role": "shadow"},
            {"id": "leaf_mid", "hex": "#4d7a2e", "ramp": "leaf"},
            {"id": "leaf_light", "hex": "#81ba47", "ramp": "leaf", "role": "light"},
            {"id": "trunk_shadow", "hex": "#292012", "ramp": "trunk", "role": "shadow"},
            {"id": "trunk_mid", "hex": "#6b4f30", "ramp": "trunk"},
            {"id": "trunk_light", "hex": "#b6835c", "ramp": "trunk", "role": "light"},
            {"id": "moss_shadow", "hex": "#2d4021", "ramp": "moss", "role": "shadow"},
            {"id": "moss_mid", "hex": "#5c7a3f", "ramp": "moss"},
            {"id": "moss_light", "hex": "#9ab671", "ramp": "moss", "role": "light"},
            {"id": "earth_shadow", "hex": "#322515", "ramp": "earth", "role": "shadow"},
            {"id": "earth_mid", "hex": "#7a5233", "ramp": "earth"},
            {"id": "earth_light", "hex": "#c1886a", "ramp": "earth", "role": "light"},
            {"id": "water_shadow", "hex": "#1b394d", "ramp": "water", "role": "shadow"},
            {"id": "water_mid", "hex": "#3a6ea5", "ramp": "water"},
            {"id": "water_light", "hex": "#8ca8d6", "ramp": "water", "role": "light"},
            {"id": "stone_shadow", "hex": "#4b4c49", "ramp": "stone", "role": "shadow"},
            {"id": "stone_mid", "hex": "#84847f", "ramp": "stone"},
            {"id": "stone_light", "hex": "#c1c1bf", "ramp": "stone", "role": "light"},
            {"id": "berry", "hex": "#a33b52", "ramp": "accent"},
            {"id": "outline", "hex": "#13110f", "role": "outline"},
        ],
    },
    "desert": {
        "id": "desert",
        "colors": [
            {"id": "sand_shadow", "hex": "#7a682d", "ramp": "sand", "role": "shadow"},
            {"id": "sand_mid", "hex": "#c2a05a", "ramp": "sand"},
            {"id": "sand_light", "hex": "#e5d2b8", "ramp": "sand", "role": "light"},
            {"id": "rock_shadow", "hex": "#524e44", "ramp": "rock", "role": "shadow"},
            {"id": "rock_mid", "hex": "#8f8578", "ramp": "rock"},
            {"id": "rock_light", "hex": "#c9c3bd", "ramp": "rock", "role": "light"},
            {"id": "leather_shadow", "hex": "#40301a", "ramp": "leather", "role": "shadow"},
            {"id": "leather_mid", "hex": "#8a5f38", "ramp": "leather"},
            {"id": "leather_light", "hex": "#ca997b", "ramp": "leather", "role": "light"},
            {"id": "cloth_shadow", "hex": "#755729", "ramp": "cloth", "role": "shadow"},
            {"id": "cloth_mid", "hex": "#c28b52", "ramp": "cloth"},
            {"id": "cloth_light", "hex": "#e8d0bf", "ramp": "cloth", "role": "light"},
            {"id": "wood_shadow", "hex": "#281e10", "ramp": "wood", "role": "shadow"},
            {"id": "wood_mid", "hex": "#6e4a2c", "ramp": "wood"},
            {"id": "wood_light", "hex": "#bc7c58", "ramp": "wood", "role": "light"},
            {"id": "oasis_shadow", "hex": "#204756", "ramp": "oasis", "role": "shadow"},
            {"id": "oasis_mid", "hex": "#3f7fa8", "ramp": "oasis"},
            {"id": "oasis_light", "hex": "#98b9d9", "ramp": "oasis", "role": "light"},
            {"id": "accent", "hex": "#7a2f28", "ramp": "accent"},
            {"id": "outline", "hex": "#13100e", "role": "outline"},
        ],
    },
}


def curated_palette_names() -> tuple[str, ...]:
    """Names of every curated palette, sorted."""
    return tuple(sorted(CURATED_PALETTES))


def load_curated_palette(name: str) -> Palette:
    """Return `name` validated against the `Palette` schema.

    Raises `PaletteError` for an unknown name, so callers never touch the raw
    dict. The returned palette is a ready-to-use material library: each material
    is a 3-tone `ramp` group plus the palette's shared `outline` colour.
    """
    data = CURATED_PALETTES.get(name)
    if data is None:
        raise PaletteError(
            f"unknown curated palette {name!r}; available: {', '.join(curated_palette_names())}"
        )
    return Palette.model_validate(data)
