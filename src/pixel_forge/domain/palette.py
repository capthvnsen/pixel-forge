"""Palette resolution: colour-id lookup, nearest-colour matching, limit checks."""

from __future__ import annotations

import re
from dataclasses import dataclass

from pixel_forge.errors import PaletteError
from pixel_forge.schemas.common import RGBA
from pixel_forge.schemas.palette import Palette

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")


def hex_to_rgba(hex_str: str) -> RGBA:
    if not _HEX_RE.match(hex_str):
        raise PaletteError(f"invalid hex colour: {hex_str!r}")
    value = hex_str[1:]
    r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    a = int(value[6:8], 16) if len(value) == 8 else 255
    return (r, g, b, a)


def rgba_to_hex(rgba: RGBA) -> str:
    r, g, b, a = rgba
    hex_str = f"#{r:02x}{g:02x}{b:02x}"
    if a != 255:
        hex_str += f"{a:02x}"
    return hex_str


@dataclass(frozen=True)
class ResolvedPalette:
    """A `Palette` with colour lookups resolved to concrete RGBA values."""

    palette: Palette

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(color.id for color in self.palette.colors)

    @property
    def size(self) -> int:
        return len(self.palette.colors)

    def rgba(self, color_id: str) -> RGBA:
        color = self.palette.by_id.get(color_id)
        if color is None:
            raise PaletteError(
                f"unknown palette color id {color_id!r} in palette {self.palette.id!r}; "
                f"valid ids: {', '.join(self.ids)}"
            )
        return hex_to_rgba(color.hex)

    def contains_rgba(self, rgba: RGBA) -> bool:
        return any(hex_to_rgba(color.hex) == rgba for color in self.palette.colors)

    def nearest(self, rgba: RGBA) -> str:
        """Nearest colour by squared-RGB distance. Ties keep the earlier declared id."""
        if not self.palette.colors:
            raise PaletteError(f"palette {self.palette.id!r} has no colors")
        r, g, b = rgba[0], rgba[1], rgba[2]
        best_id = self.palette.colors[0].id
        best_dist: int | None = None
        for color in self.palette.colors:
            cr, cg, cb, _ = hex_to_rgba(color.hex)
            dist = (cr - r) ** 2 + (cg - g) ** 2 + (cb - b) ** 2
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best_id = color.id
        return best_id


def resolve_palette(palette: Palette) -> ResolvedPalette:
    return ResolvedPalette(palette=palette)


def check_palette_limit(palette: Palette, limit: int) -> list[str]:
    """Colour ids beyond `limit` (declaration order), empty when within limit."""
    return [color.id for color in palette.colors[limit:]]
