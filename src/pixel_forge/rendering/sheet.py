"""Sprite sheet / atlas packing, contact sheets, and tileset seam checking.

`ResolvedFrame` (from `pixel_forge.animation.resolver`, written concurrently) is only ever
referenced as a type annotation here — imported under `TYPE_CHECKING` and never at runtime,
per `from __future__ import annotations` postponed evaluation. Frame objects are only ever
read structurally: `.direction`, `.animation`, `.index`, `.duration_ms`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import numpy as np
from numpy.typing import NDArray

from pixel_forge.errors import ForgeError
from pixel_forge.rendering.canvas import RGBA, Canvas
from pixel_forge.rendering.font import draw_text, text_width

if TYPE_CHECKING:
    from pixel_forge.animation.resolver import ResolvedFrame

Edge = Literal["N", "S", "E", "W"]
_OPPOSITE: dict[Edge, Edge] = {"N": "S", "S": "N", "E": "W", "W": "E"}


@dataclass(frozen=True)
class SheetCell:
    direction: str
    animation: str
    index: int
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class SpriteSheet:
    image: Canvas
    cells: tuple[SheetCell, ...]
    columns: int
    rows: int

    def cell_for(self, animation: str, direction: str, index: int) -> SheetCell:
        for cell in self.cells:
            if cell.animation == animation and cell.direction == direction and cell.index == index:
                return cell
        raise ForgeError(f"no sheet cell for {animation}/{direction}#{index}")


def build_sprite_sheet(
    frames: Sequence[tuple[ResolvedFrame, Canvas]],
    canvas_size: tuple[int, int],
    columns: int | None = None,
) -> SpriteSheet:
    """Pack `frames` row-major, grouped by (animation, direction) in input order — each group
    starts a new row, so a row never mixes animations. `columns=None` uses the widest group's
    frame count. Frames whose canvas isn't exactly `canvas_size` raise `ForgeError`."""
    if not frames:
        raise ForgeError("build_sprite_sheet: frames must be non-empty")
    cw, ch = canvas_size

    groups: dict[tuple[str, str], list[tuple[ResolvedFrame, Canvas]]] = {}
    for frame, canvas in frames:
        if (canvas.width, canvas.height) != (cw, ch):
            raise ForgeError(
                f"frame {frame.animation}/{frame.direction}#{frame.index} has size "
                f"{canvas.width}x{canvas.height}, expected {cw}x{ch}"
            )
        groups.setdefault((frame.animation, frame.direction), []).append((frame, canvas))

    if columns is None:
        columns = max(len(group) for group in groups.values())
    if columns < 1:
        raise ForgeError("build_sprite_sheet: columns must be >= 1")

    cells: list[SheetCell] = []
    placements: list[tuple[int, int, Canvas]] = []
    row_cursor = 0
    for (animation, direction), group in groups.items():
        for j, (frame, canvas) in enumerate(group):
            x = (j % columns) * cw
            y = (row_cursor + j // columns) * ch
            cells.append(
                SheetCell(
                    direction=direction,
                    animation=animation,
                    index=frame.index,
                    x=x,
                    y=y,
                    w=cw,
                    h=ch,
                )
            )
            placements.append((x, y, canvas))
        row_cursor += -(-len(group) // columns)  # ceil division

    total_rows = row_cursor
    sheet_image = Canvas(columns * cw, total_rows * ch)
    for x, y, canvas in placements:
        sheet_image.blit(canvas, (x, y))

    return SpriteSheet(image=sheet_image, cells=tuple(cells), columns=columns, rows=total_rows)


def build_contact_sheet(
    sheet: SpriteSheet,
    labels: Mapping[tuple[str, str], str] | None = None,
    scale: int = 1,
    background: RGBA = (24, 24, 32, 255),
) -> Canvas:
    """Render `sheet` with a 1px separator grid, an opaque background, and a bitmap-font
    label per row (default `f"{animation}/{direction}"`)."""
    labels = labels or {}
    cw = sheet.image.width // sheet.columns
    ch = sheet.image.height // sheet.rows

    row_labels: dict[int, str] = {}
    for cell in sheet.cells:
        row = cell.y // ch
        if row not in row_labels:
            row_labels[row] = labels.get(
                (cell.animation, cell.direction), f"{cell.animation}/{cell.direction}"
            )

    gutter = (max(text_width(t) for t in row_labels.values()) + 4) if row_labels else 0
    cell_w = cw * scale + 1
    cell_h = ch * scale + 1
    width = gutter + 1 + sheet.columns * cell_w
    height = 1 + sheet.rows * cell_h

    canvas = Canvas(width, height)
    canvas.draw_rect((0, 0), (width, height), background, fill=True)

    ink: RGBA = (255, 255, 255, 255)
    for row, label in row_labels.items():
        draw_text(canvas, label, (2, 1 + row * cell_h), ink)

    for cell in sheet.cells:
        col = cell.x // cw
        row = cell.y // ch
        sub = Canvas(cw, ch)
        sub.array[:] = sheet.image.array[cell.y : cell.y + ch, cell.x : cell.x + cw]
        if scale > 1:
            sub = sub.scale(scale)
        canvas.blit(sub, (gutter + 1 + col * cell_w, 1 + row * cell_h))

    return canvas


def build_atlas(
    images: Mapping[str, Canvas],
    columns: int | None = None,
    *,
    rows: Sequence[Sequence[str]] | None = None,
) -> tuple[Canvas, dict[str, SheetCell]]:
    """Uniform-grid atlas for tiles. Every returned `SheetCell` has `animation` and
    `direction` both set to the key, `index=0`.

    Default (`rows=None`): keys iterated in sorted order, packed into `columns`-wide
    rows (one row when `columns` is `None`) -- unchanged from before `rows` existed.

    Explicit layout (`rows` given): each inner sequence is one atlas row, laid out left
    to right in exactly that order; a row shorter than the widest row is padded with
    empty cells. The set of ids across `rows` must equal `images`'s keys exactly, or
    `ForgeError` is raised. `columns` is ignored in this mode.
    """
    if not images:
        raise ForgeError("build_atlas: images must be non-empty")
    sizes = {(img.width, img.height) for img in images.values()}
    if len(sizes) > 1:
        raise ForgeError(f"build_atlas: images must share one size, got {sorted(sizes)}")
    cw, ch = next(iter(sizes))

    if rows is not None:
        row_ids = {tile_id for row in rows for tile_id in row}
        image_ids = set(images)
        if row_ids != image_ids:
            raise ForgeError(
                "build_atlas: rows layout ids must match images exactly "
                f"(missing from rows: {sorted(image_ids - row_ids)}, "
                f"unknown in rows: {sorted(row_ids - image_ids)})"
            )
        n_columns = max(len(row) for row in rows)
        atlas = Canvas(n_columns * cw, len(rows) * ch)
        row_cells: dict[str, SheetCell] = {}
        for y, row in enumerate(rows):
            for x, tile_id in enumerate(row):
                px, py = x * cw, y * ch
                atlas.blit(images[tile_id], (px, py))
                row_cells[tile_id] = SheetCell(
                    direction=tile_id, animation=tile_id, index=0, x=px, y=py, w=cw, h=ch
                )
        return atlas, row_cells

    keys = sorted(images)
    if columns is None:
        columns = len(keys)
    if columns < 1:
        raise ForgeError("build_atlas: columns must be >= 1")
    n_rows = -(-len(keys) // columns)

    atlas = Canvas(columns * cw, n_rows * ch)
    cells: dict[str, SheetCell] = {}
    for i, key in enumerate(keys):
        x, y = (i % columns) * cw, (i // columns) * ch
        atlas.blit(images[key], (x, y))
        cells[key] = SheetCell(direction=key, animation=key, index=0, x=x, y=y, w=cw, h=ch)
    return atlas, cells


@dataclass(frozen=True)
class SeamResult:
    tile_a: str
    tile_b: str
    edge: Edge
    mismatched_pixels: int


def _edge_pixels(canvas: Canvas, edge: Edge) -> NDArray[np.uint8]:
    arr = canvas.array
    if edge == "N":
        return arr[0, :, :]
    if edge == "S":
        return arr[-1, :, :]
    if edge == "W":
        return arr[:, 0, :]
    return arr[:, -1, :]


def _count_mismatch(a: NDArray[np.uint8], b: NDArray[np.uint8]) -> int:
    n = min(len(a), len(b))
    mismatched = int(np.count_nonzero(np.any(a[:n] != b[:n], axis=-1)))
    return mismatched + abs(len(a) - len(b))


def check_seams(tiles: Mapping[str, Canvas]) -> list[SeamResult]:
    """For every ordered tile pair (self-pairs included) and each of N/S/E/W, compare tile A's
    edge against tile B's opposite edge (the edge that would abut it when tiled), counting
    mismatched pixels. Ordering: sorted tile ids, then N, S, E, W."""
    ids = sorted(tiles)
    edges: tuple[Edge, ...] = ("N", "S", "E", "W")
    results: list[SeamResult] = []
    for a in ids:
        for b in ids:
            for edge in edges:
                a_edge = _edge_pixels(tiles[a], edge)
                b_edge = _edge_pixels(tiles[b], _OPPOSITE[edge])
                results.append(
                    SeamResult(
                        tile_a=a,
                        tile_b=b,
                        edge=edge,
                        mismatched_pixels=_count_mismatch(a_edge, b_edge),
                    )
                )
    return results


def build_seam_map(tiles: Mapping[str, Canvas], layout: Sequence[Sequence[str]]) -> Canvas:
    """Render `layout` (a grid of tile ids) into one canvas for human seam inspection. Cell
    size is taken from the first referenced tile; unknown tile ids raise `ForgeError`."""
    if not layout or not layout[0]:
        raise ForgeError("build_seam_map: layout must be non-empty")
    for row in layout:
        for tile_id in row:
            if tile_id not in tiles:
                raise ForgeError(f"build_seam_map: unknown tile id {tile_id!r}")

    tw, th = tiles[layout[0][0]].width, tiles[layout[0][0]].height
    row_count = len(layout)
    col_count = max(len(row) for row in layout)
    canvas = Canvas(col_count * tw, row_count * th)
    for ry, row in enumerate(layout):
        for cx, tile_id in enumerate(row):
            canvas.blit(tiles[tile_id], (cx * tw, ry * th))
    return canvas
