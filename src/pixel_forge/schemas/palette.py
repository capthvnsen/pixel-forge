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


class Palette(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    colors: list[PaletteColor] = Field(default_factory=list)

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
