"""PNG ingestion: turn real pixels into a palette-indexed `bitmap` shape.

The shape DSL (`rect`/`ellipse`/...) cannot produce readable pixel art. This module is
the front door for real pixels from any source -- Aseprite, a diffusion model, a vision
model -- converting a raster image into the `bitmap` shape op that `rendering/shapes.py`
draws.

`BitmapShape` lives in `schemas/common.py` and is owned by a concurrent task, so this
module never imports it at runtime: `png_to_bitmap` returns a plain `dict` shaped
exactly like the model (`{"op": "bitmap", "at": [x, y], "key": {...}, "rows": [...]}`).
The caller (the API layer) validates it, e.g. `BitmapShape.model_validate(bitmap)`.

Pure functions only: no project layout or filesystem knowledge beyond `load_image`
reading a file.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from pixel_forge.domain.palette import ResolvedPalette, rgba_to_hex
from pixel_forge.errors import ForgeError
from pixel_forge.schemas.palette import Palette, PaletteColor

_POOL = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


@dataclass(frozen=True)
class IngestReport:
    """What `png_to_bitmap` found, so the caller can decide what to do about it."""

    width: int
    height: int
    matched: int
    snapped: dict[str, int]
    unmatched: dict[str, int]
    added_colors: tuple[str, ...]
    trimmed_to: tuple[int, int, int, int] | None


def assign_chars(palette: ResolvedPalette) -> dict[str, str]:
    """Deterministic colour-id -> single-char map, in the palette's declared order.

    For each colour id, in declaration order, the first free character is chosen from:
    the id's own letters lowercased (in id order), then the id's own letters
    uppercased (in id order), then the id's own digits (in id order). If none of those
    are free, the first free character of `_POOL` (lowercase, then uppercase, then
    digits) is used instead. `.` and space are never candidates -- they are not
    alphanumeric and not in `_POOL` -- so they stay reserved for transparency.

    Pure function of `palette.ids`: the same palette produces the same map in any
    process, since candidate selection only ever tests set membership, never iterates
    a set or dict to produce output order.

    Raises `ForgeError` if the palette declares more colours than `_POOL` has
    characters (62: 26 lowercase + 26 uppercase + 10 digits).
    """
    taken: set[str] = set()
    assigned: dict[str, str] = {}
    for color_id in palette.ids:
        letters = [c for c in color_id if c.isalpha()]
        digits = [c for c in color_id if c.isdigit()]
        candidates = [c.lower() for c in letters] + [c.upper() for c in letters] + digits
        chosen = next((c for c in candidates if c not in taken), None)
        if chosen is None:
            chosen = next((c for c in _POOL if c not in taken), None)
        if chosen is None:
            raise ForgeError(
                f"palette {palette.palette.id!r} has {palette.size} colours, more than "
                f"the {len(_POOL)} characters assignable to a bitmap key"
            )
        taken.add(chosen)
        assigned[color_id] = chosen
    return assigned


def _to_array(image: Image.Image) -> NDArray[np.uint8]:
    return np.array(image.convert("RGBA"), dtype=np.uint8)


def png_to_bitmap(
    image: Image.Image,
    palette: ResolvedPalette,
    *,
    snap: bool = False,
    trim: bool = True,
) -> tuple[dict[str, Any], IngestReport]:
    """Convert a raster image into a palette-indexed `bitmap` shape (as a plain dict).

    Alpha is binary: source alpha >= 128 is opaque, below is transparent.

    An opaque pixel whose RGB exactly equals a palette colour is `matched`. Otherwise,
    with `snap=True` it is mapped to `ResolvedPalette.nearest` and counted in
    `IngestReport.snapped` keyed by its source hex; with `snap=False` (the default) it
    is rendered transparent and counted in `IngestReport.unmatched`. This never raises
    on unmatched pixels -- the report is the caller's signal to decide what to do.

    `trim=True` (default) crops to the opaque bounding box; `IngestReport.trimmed_to`
    reports that box as half-open `(x0, y0, x1, y1)` in source coordinates, so the
    caller can compute `at` relative to a region anchor. A fully transparent image with
    `trim=True` raises `ForgeError`, since there is nothing to import. `trim=False`
    keeps the full source size and `trimmed_to` is `None`.

    The returned dict's `"at"` is always `[0, 0]` -- this function has no anchor to
    place the bitmap against, so the caller repositions it using `trimmed_to`. Only
    genuinely broken input raises: an empty image, or a palette with no colours.
    """
    if palette.size == 0:
        raise ForgeError(f"palette {palette.palette.id!r} has no colors to import against")
    if image.width <= 0 or image.height <= 0:
        raise ForgeError("image has no pixels")

    arr = _to_array(image)
    opaque = arr[..., 3] >= 128

    trimmed_to: tuple[int, int, int, int] | None = None
    if trim:
        if not opaque.any():
            raise ForgeError("image is fully transparent; nothing to import")
        ys, xs = np.nonzero(opaque)
        x0, y0 = int(xs.min()), int(ys.min())
        x1, y1 = int(xs.max()) + 1, int(ys.max()) + 1
        arr = arr[y0:y1, x0:x1]
        opaque = opaque[y0:y1, x0:x1]
        trimmed_to = (x0, y0, x1, y1)

    height, width = arr.shape[:2]
    chars = assign_chars(palette)
    rgb_lookup: dict[tuple[int, int, int], str] = {}
    for pid in palette.ids:
        r, g, b, _a = palette.rgba(pid)
        rgb_lookup.setdefault((r, g, b), pid)

    key: dict[str, str] = {}
    rows: list[str] = []
    matched = 0
    snapped: dict[str, int] = {}
    unmatched: dict[str, int] = {}
    for y in range(height):
        row_chars: list[str] = []
        for x in range(width):
            if not opaque[y, x]:
                row_chars.append(".")
                continue
            r, g, b, _a = (int(c) for c in arr[y, x])
            color_id: str | None = rgb_lookup.get((r, g, b))
            if color_id is not None:
                matched += 1
            elif snap:
                color_id = palette.nearest((r, g, b, 255))
                hex_src = rgba_to_hex((r, g, b, 255))
                snapped[hex_src] = snapped.get(hex_src, 0) + 1
            else:
                hex_src = rgba_to_hex((r, g, b, 255))
                unmatched[hex_src] = unmatched.get(hex_src, 0) + 1
                row_chars.append(".")
                continue
            char = chars[color_id]
            key.setdefault(char, color_id)
            row_chars.append(char)
        rows.append("".join(row_chars))

    bitmap: dict[str, Any] = {"op": "bitmap", "at": [0, 0], "key": key, "rows": rows}
    report = IngestReport(
        width=width,
        height=height,
        matched=matched,
        snapped=snapped,
        unmatched=unmatched,
        added_colors=(),
        trimmed_to=trimmed_to,
    )
    return bitmap, report


def extract_palette(
    image: Image.Image, *, max_colors: int = 24, palette_id: str = "imported"
) -> Palette:
    """Build a `Palette` from an image's most frequent opaque colours.

    Alpha is binary: source alpha >= 128 is opaque; transparent pixels are excluded
    from the count. Colours are ordered by descending pixel count, ties broken by
    ascending hex string, so the result does not depend on PIL's internal pixel
    iteration order. Ids are generated as `c00`, `c01`, ... in that resulting order.
    """
    arr = _to_array(image)
    opaque = arr[..., 3] >= 128
    counts: dict[str, int] = {}
    for r, g, b, _a in arr[opaque].tolist():
        hex_str = rgba_to_hex((r, g, b, 255))
        counts[hex_str] = counts.get(hex_str, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:max_colors]
    colors = [
        PaletteColor(id=f"c{i:02d}", hex=hex_str) for i, (hex_str, _count) in enumerate(ordered)
    ]
    return Palette(id=palette_id, colors=colors)


def load_image(path: Path) -> Image.Image:
    """Open a PNG and convert to RGBA. Raises `ForgeError` naming the path on failure.

    No path-safety checks here -- the API layer resolves and validates paths before
    this is ever called.
    """
    try:
        with Image.open(path) as img:
            rgba = img.convert("RGBA")
    except OSError as exc:
        raise ForgeError(f"could not read image at {path}: {exc}") from exc
    return rgba
