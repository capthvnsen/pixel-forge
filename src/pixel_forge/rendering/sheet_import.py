"""Grid-sheet import: slice one hand-authored 8-directional sheet into per-direction frames.

A diffusion model or artist producing pixel art outside this toolkit's shape DSL often
delivers it as a single grid sheet: a compass layout (8 directions around an empty
centre cell, or 4 cardinal directions) upscaled for review. `slice_sheet` turns that one
image into the same shape `api.import_sheet` needs to write real per-direction frame
PNGs: cropped to each sprite's opaque bounding box, then baseline-aligned onto a square
canvas exactly the way the rest of the toolkit expects (`ANI001`'s baseline-drift check
passes because every direction is placed by the same rule).

**The `compass8` vertical convention is deliberate, not arbitrary**: verified against a
real 3x3 compass sheet where the *top* row shows the camera-facing side (the visor is
visible) and the *bottom* row shows backs. So the top row maps to `south` and the bottom
row to `north`. A sheet authored with the opposite convention (top row = away from
camera) uses `compass8-flipped`, which swaps only the north/south rows -- `west`/`east`
(the middle row) read the same either way.

Pure and testable: takes a PIL image plus `SheetImportOptions` and returns a
`SheetImportReport` of `Canvas` frames plus an extracted palette. No project layout, no
filesystem writes, no CLI/MCP knowledge -- `api.import_sheet` owns turning that into
files on disk and a pinned asset spec.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from pixel_forge.errors import ForgeError
from pixel_forge.rendering.canvas import Canvas
from pixel_forge.rendering.ingest import extract_palette
from pixel_forge.schemas.palette import Palette

Layout = Literal["compass8", "compass8-flipped", "compass4"]

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# Top row = camera-facing (south), bottom row = backs (north) -- see module docstring.
_COMPASS8: tuple[tuple[str | None, ...], ...] = (
    ("south_west", "south", "south_east"),
    ("west", None, "east"),
    ("north_west", "north", "north_east"),
)

# compass4 lays these four names out row-major over whatever grid shape (2x2 or 1x4)
# the sheet uses -- e.g. a 2x2 grid reads south/west on row 0, east/north on row 1.
_COMPASS4_NAMES = ("south", "west", "east", "north")


@dataclass(frozen=True)
class SheetImportOptions:
    grid: tuple[int, int] | None = None  # (cols, rows)
    cell: tuple[int, int] | None = None  # (width, height)
    layout: Layout | None = None
    directions: tuple[str, ...] | None = None
    scale: int = 1
    canvas: int = 48
    baseline: int = 44
    background: str = "auto"  # "auto" | "transparent" | "#rrggbb"
    frames_per_cell: int = 1
    palette_limit: int = 24

    def __post_init__(self) -> None:
        if (self.grid is None) == (self.cell is None):
            raise ForgeError("exactly one of --grid or --cell must be given")
        if (self.layout is None) == (self.directions is None):
            raise ForgeError("exactly one of --layout or --directions must be given")


@dataclass(frozen=True)
class DirectionFrame:
    direction: str
    index: int
    canvas: Canvas


@dataclass(frozen=True)
class SheetImportReport:
    directions: tuple[str, ...]  # in the order encountered, row-major over the grid
    cells_total: int
    cells_skipped: int
    frames: tuple[DirectionFrame, ...]
    palette: Palette


# --- scale verification / downscale --------------------------------------------------------


def _downscale_exact(image: Image.Image, scale: int) -> Image.Image:
    """Downscale by exactly `scale`, after verifying every `scale`x`scale` block is a
    single uniform colour. Raises `ForgeError` naming the first offending block
    (row-major) otherwise -- silently downscaling a non-uniform block would destroy
    pixels rather than just shrink them.
    """
    width, height = image.size
    if width % scale != 0 or height % scale != 0:
        raise ForgeError(f"sheet is {width}x{height}px, not an exact multiple of --scale {scale}")
    arr = np.array(image, dtype=np.uint8)
    new_h, new_w = height // scale, width // scale
    blocks = arr.reshape(new_h, scale, new_w, scale, 4)
    reference = blocks[:, :1, :, :1, :]
    uniform: NDArray[np.bool_] = np.all(blocks == reference, axis=(1, 3, 4))
    if not bool(uniform.all()):
        bad = np.argwhere(~uniform)
        row, col = int(bad[0, 0]), int(bad[0, 1])
        raise ForgeError(
            f"sheet block at (col={col}, row={row}) is not a uniform {scale}x{scale} colour; "
            f"downscaling by --scale {scale} would destroy pixels there"
        )
    return image.resize((new_w, new_h), Image.Resampling.NEAREST)


# --- background removal ---------------------------------------------------------------------


def _resolve_background(image: Image.Image, background: str) -> tuple[int, int, int, int] | None:
    """The background RGBA to strip, or `None` when the sheet already carries alpha."""
    if background == "transparent":
        return None
    if background == "auto":
        arr = np.array(image, dtype=np.uint8).reshape(-1, 4)
        colors, counts = np.unique(arr, axis=0, return_counts=True)
        r, g, b, a = (int(v) for v in colors[int(np.argmax(counts))])
        return (r, g, b, a)
    if not _HEX_RE.match(background):
        raise ForgeError(
            f"--background must be 'auto', 'transparent', or '#rrggbb', got {background!r}"
        )
    r, g, b = (int(background[i : i + 2], 16) for i in (1, 3, 5))
    return (r, g, b, 255)


def _remove_background(image: Image.Image, background: str) -> Image.Image:
    """Every pixel exactly equal to the resolved background becomes transparent; every
    other pixel becomes fully opaque. Binary alpha throughout, matching `Canvas`.
    """
    arr = np.array(image, dtype=np.uint8).copy()
    bg = _resolve_background(image, background)
    if bg is None:
        opaque = arr[..., 3] >= 128
    else:
        opaque = ~np.all(arr == np.array(bg, dtype=np.uint8), axis=-1)
    arr[..., 3] = np.where(opaque, 255, 0)
    return Image.fromarray(arr, mode="RGBA")


# --- grid slicing ----------------------------------------------------------------------------


def _grid_dims(image: Image.Image, options: SheetImportOptions) -> tuple[int, int, int, int]:
    """(cols, rows, cell_width, cell_height), raising if the sheet does not divide evenly."""
    width, height = image.size
    if options.grid is not None:
        cols, rows = options.grid
        if width % cols != 0 or height % rows != 0:
            raise ForgeError(
                f"sheet is {width}x{height}px, which does not divide evenly into a "
                f"{cols}x{rows} grid"
            )
        return cols, rows, width // cols, height // rows
    assert options.cell is not None
    cell_w, cell_h = options.cell
    if width % cell_w != 0 or height % cell_h != 0:
        raise ForgeError(
            f"sheet is {width}x{height}px, which does not divide evenly into "
            f"{cell_w}x{cell_h} cells"
        )
    return width // cell_w, height // cell_h, cell_w, cell_h


def _layout_grid(layout: Layout, rows: int, cols: int) -> tuple[tuple[str | None, ...], ...]:
    if layout == "compass4":
        if (rows, cols) not in ((2, 2), (1, 4)):
            raise ForgeError(f"layout 'compass4' needs a 2x2 or 1x4 grid, got {cols}x{rows}")
        names = list(_COMPASS4_NAMES)
        return tuple(tuple(names[r * cols : (r + 1) * cols]) for r in range(rows))
    grid = _COMPASS8 if layout == "compass8" else tuple(reversed(_COMPASS8))
    if (len(grid), len(grid[0])) != (rows, cols):
        raise ForgeError(f"layout {layout!r} needs a 3x3 grid, got {cols}x{rows}")
    return grid


def _opaque_mask(image: Image.Image) -> NDArray[np.bool_]:
    arr: NDArray[np.uint8] = np.array(image, dtype=np.uint8)
    mask: NDArray[np.bool_] = arr[..., 3] != 0
    return mask


def _mask_bbox(mask: NDArray[np.bool_]) -> tuple[int, int, int, int] | None:
    if not mask.any():
        return None
    ys, xs = np.nonzero(mask)
    return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)


def _place_direction(
    direction: str, cell_image: Image.Image, options: SheetImportOptions
) -> list[DirectionFrame]:
    """Split `cell_image` into `frames_per_cell` equal-width strips, crop every strip to
    the union of all strips' opaque bounding boxes (so the group shares one alignment
    and does not jitter between animation frames), and baseline/centre it onto a fresh
    `canvas`x`canvas` square per strip.
    """
    n = options.frames_per_cell
    cell_w, cell_h = cell_image.size
    if cell_w % n != 0:
        raise ForgeError(
            f"cell width {cell_w}px is not evenly divisible by --frames-per-cell {n} "
            f"(direction {direction!r})"
        )
    strip_w = cell_w // n
    strips = [cell_image.crop((i * strip_w, 0, (i + 1) * strip_w, cell_h)) for i in range(n)]
    union_mask = _opaque_mask(strips[0])
    for strip in strips[1:]:
        union_mask = union_mask | _opaque_mask(strip)
    bbox = _mask_bbox(union_mask)
    if bbox is None:
        return []

    x0, y0, x1, y1 = bbox
    bbox_w, bbox_h = x1 - x0, y1 - y0
    size = options.canvas
    dest_x = (size - bbox_w) // 2
    dest_y = options.baseline - bbox_h + 1

    frames: list[DirectionFrame] = []
    for index, strip in enumerate(strips):
        cropped = strip.crop((x0, y0, x1, y1))
        canvas = Canvas(size, size)
        canvas.blit(Canvas.from_image(cropped), (dest_x, dest_y))
        frames.append(DirectionFrame(direction=direction, index=index, canvas=canvas))
    return frames


def _extract_palette(frames: Sequence[DirectionFrame], limit: int) -> Palette:
    composite = np.concatenate([f.canvas.array for f in frames], axis=1)
    full = extract_palette(Image.fromarray(composite, mode="RGBA"), max_colors=1_000_000)
    if len(full.colors) > limit:
        raise ForgeError(
            f"imported art uses {len(full.colors)} colour(s), exceeding --palette-limit {limit}"
        )
    return full


# --- entry point -----------------------------------------------------------------------------


def slice_sheet(image: Image.Image, options: SheetImportOptions) -> SheetImportReport:
    image = image.convert("RGBA")
    if options.scale > 1:
        image = _downscale_exact(image, options.scale)
    image = _remove_background(image, options.background)

    cols, rows, cell_w, cell_h = _grid_dims(image, options)
    cells_total = rows * cols
    cells = [
        (r, c, image.crop((c * cell_w, r * cell_h, (c + 1) * cell_w, (r + 1) * cell_h)))
        for r in range(rows)
        for c in range(cols)
    ]

    frames: list[DirectionFrame] = []
    directions_found: list[str] = []
    cells_skipped = 0

    if options.directions is not None:
        nonempty = [(r, c, img) for r, c, img in cells if bool(_opaque_mask(img).any())]
        cells_skipped = cells_total - len(nonempty)
        if len(nonempty) != len(options.directions):
            raise ForgeError(
                f"--directions lists {len(options.directions)} name(s) but the sheet has "
                f"{len(nonempty)} non-empty cell(s)"
            )
        for (_r, _c, cell_image), direction in zip(nonempty, options.directions, strict=True):
            frames.extend(_place_direction(direction, cell_image, options))
            directions_found.append(direction)
    else:
        assert options.layout is not None
        grid = _layout_grid(options.layout, rows, cols)
        for r, c, cell_image in cells:
            label = grid[r][c]
            if label is None or not bool(_opaque_mask(cell_image).any()):
                cells_skipped += 1
                continue
            frames.extend(_place_direction(label, cell_image, options))
            directions_found.append(label)

    if not frames:
        raise ForgeError("no non-empty cells were found in the sheet; nothing to import")

    palette = _extract_palette(frames, options.palette_limit)
    return SheetImportReport(
        directions=tuple(directions_found),
        cells_total=cells_total,
        cells_skipped=cells_skipped,
        frames=tuple(frames),
        palette=palette,
    )
