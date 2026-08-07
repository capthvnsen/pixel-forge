"""Palette definitions: named colours a spec's shapes reference by id."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pixel_forge.errors import PaletteError
from pixel_forge.schemas.common import RGBA

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")


class PaletteColor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    hex: str = Field(pattern=_HEX_RE.pattern)
    role: str | None = None
    ramp: str | None = None
    # How many tonal steps an auto-ramp should generate from this colour's hex
    # (the hex is the mid step) when the palette opts into `auto_ramp`. 1 keeps
    # the colour flat; generated ramp steps carry ramp_steps=1 so re-expansion
    # is a no-op. Default 3 = shadow/mid/highlight, the professional minimum.
    ramp_steps: int = Field(default=3, ge=1, le=7)


class Palette(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    colors: list[PaletteColor] = Field(default_factory=list)
    # Opt-in colour discipline: when true, each colour expands to `ramp_steps`
    # tones (shadow..highlight) via domain.palette.expand_palette, and a
    # hue-tinted charcoal `outline` colour is derived from the palette's darkest
    # colour. Both default false so existing specs parse and resolve unchanged.
    auto_ramp: bool = False
    derive_outline: bool = False

    @model_validator(mode="after")
    def _check_unique_ids(self) -> Palette:
        seen: set[str] = set()
        for color in self.colors:
            if color.id in seen:
                raise ValueError(f"duplicate palette color id: {color.id!r}")
            seen.add(color.id)
        return self

    @property
    def by_id(self) -> dict[str, PaletteColor]:
        return {color.id: color for color in self.colors}

    def to_rgba(self, color_id: str) -> RGBA:
        color = self.by_id.get(color_id)
        if color is None:
            raise PaletteError(f"unknown palette color id: {color_id!r} in palette {self.id!r}")
        return _hex_to_rgba(color.hex)


# AssetDoc.palette embeds a full palette (id + colors) rather than referencing one
# by id alone, so PaletteRef is just Palette under the interface's documented name.
PaletteRef = Palette


def _hex_to_rgba(hex_str: str) -> RGBA:
    value = hex_str.lstrip("#")
    r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    a = int(value[6:8], 16) if len(value) == 8 else 255
    return (r, g, b, a)
