"""External frame sources: pinned PNGs standing in for the shape DSL.

A sprite asset that declares `source:` gets its pixels from files on disk rather than
from `regions`. That is the supported path for art produced somewhere else -- a
diffusion model, a pixel artist, an older pipeline -- while still getting the rest of
the toolkit: validation rules, sheet packing, per-direction pivots, the Godot manifest,
and a revision log.

`pins` is what keeps `RenderBackend`'s determinism contract honest for pixels this
repo did not draw. Every referenced file's sha256 is recorded in the spec, so changing
the art on disk changes the *document's* content hash exactly as editing a shape
would. Caching, revision hashes and "render twice, compare bytes" all keep working
without knowing that a backend read a file, and art that changes underneath a spec is
a loud error instead of a silent redefinition.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

_PLACEHOLDERS = ("animation", "direction", "index")
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


class ExternalSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frames_dir: str = "frames"
    pattern: str = "{animation}_{direction}_{index}.png"
    pins: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_pattern(self) -> ExternalSource:
        found = set(_PLACEHOLDER_RE.findall(self.pattern))
        unknown = found - set(_PLACEHOLDERS)
        if unknown:
            raise ValueError(
                f"source.pattern has unknown placeholder(s) {sorted(unknown)}; "
                f"supported: {list(_PLACEHOLDERS)}"
            )
        missing = set(_PLACEHOLDERS) - found
        if missing:
            raise ValueError(
                "source.pattern must reference every frame coordinate; missing "
                f"{sorted('{' + m + '}' for m in missing)}"
            )
        # The pattern names a file, never a path: directory nesting belongs in
        # frames_dir, which is the part safe_join validates against the asset dir.
        if "/" in self.pattern or "\\" in self.pattern:
            raise ValueError(
                f"source.pattern must be a bare filename, got {self.pattern!r}; "
                "put any subdirectory in source.frames_dir"
            )
        return self

    def filename(self, animation: str, direction: str, index: int) -> str:
        return (
            self.pattern.replace("{animation}", animation)
            .replace("{direction}", direction)
            .replace("{index}", str(index))
        )


def pin_key(animation: str, direction: str, index: int) -> str:
    """Key under which one frame's file digest is recorded in `ExternalSource.pins`.

    Only *authored* frames are pinned. A mirrored direction has no file of its own --
    it is its source direction's raster flipped -- so pinning it would record the same
    digest twice under two names and drift apart on the next re-pin.
    """
    return f"{animation}_{direction}_{index}"
