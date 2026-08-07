"""Sprite sheet / atlas packing, contact sheets, and tileset seam checking.

`ResolvedFrame` (from `pixel_forge.animation.resolver`, written concurrently) is only ever
referenced as a type annotation here — imported under `TYPE_CHECKING` and never at runtime,
per `from __future__ import annotations` postponed evaluation. Frame objects are only ever
read structurally: `.direction`, `.animation`, `.index`, `.duration_ms`.
"""

from __future__ import annotations

import hashlib
import random
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import numpy as np
from numpy.typing import NDArray

from pixel_forge.domain.palette import ResolvedPalette, hex_to_rgba
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


def _material_detail_colors(base_tile: Canvas, palette: ResolvedPalette) -> list[RGBA]:
    """The base tile's dominant interior colour's palette ramp tones (dark
    tones first, then light), excluding the base colour itself.

    This is what makes generated variants *organic* instead of a palette
    swap: a grass tile's variants scatter its own dark/light greens, a dirt
    tile's scatter its browns — the interior's material dictates the detail,
    never a foreign colour (no water-blue pixels on grass). Empty when the
    palette has no ramp tones for the dominant colour (a flat palette)."""
    arr = base_tile.array
    h, w = arr.shape[:2]
    if h < 3 or w < 3:
        return []
    interior = arr[1 : h - 1, 1 : w - 1]
    opaque = interior[..., 3] != 0
    if not opaque.any():
        return []
    rgb = interior[opaque][:, :3].reshape(-1, 3)
    uniq, counts = np.unique(rgb, axis=0, return_counts=True)
    top = uniq[int(np.argmax(counts))]
    dominant: tuple[int, int, int] = (int(top[0]), int(top[1]), int(top[2]))
    color_id = palette.nearest((*dominant, 255))
    tones: list[RGBA] = []
    for suffix in ("shadow", "dark", "deep", "light", "bright", "glow"):
        tone_id = f"{color_id}_{suffix}"
        if tone_id in palette.ids:
            r, g, b, a = palette.rgba(tone_id)
            tones.append((int(r), int(g), int(b), int(a)))
    return tones


def _variation_detail_colors(
    palette: ResolvedPalette, base_tile: Canvas | None = None
) -> list[RGBA]:
    """Palette colours to scatter as variation detail.

    `light`/`shadow` role colours first (the polish-expanded palette stamps
    these roles on every material's ramp extremes). When the palette has no
    role colours, derive detail from the BASE TILE's own material via
    `_material_detail_colors` — so the pipeline's grass variants scatter
    dark/light greens and its dirt variants scatter browns. Falls back to
    every non-outline colour (a degenerate flat palette) so flat palettes
    still work. Declaration order keeps the result deterministic.
    """
    preferred = [
        hex_to_rgba(color.hex)
        for color in palette.palette.colors
        if color.role in ("light", "shadow")
    ]
    if preferred:
        return preferred
    if base_tile is not None:
        derived = _material_detail_colors(base_tile, palette)
        if derived:
            return derived
    return [hex_to_rgba(color.hex) for color in palette.palette.colors if color.role != "outline"]


# Organic 2-4px tuft micro-shapes (dx, dy) offsets. Every shape's max offset is
# 1, so a cluster anchored inside [1, w-3] x [1, h-3] stays strictly inside the
# 1px border ring — variant seams stay byte-identical to the base tile.
_TUFT_SHAPES: tuple[tuple[tuple[int, int], ...], ...] = (
    ((0, 0), (1, 0), (0, 1)),  # 3px L tuft
    ((0, 0), (1, 0), (0, 1), (1, 1)),  # 2x2 clump
    ((0, 0), (1, 0)),  # 2px horizontal blade
    ((0, 0), (0, 1)),  # 2px vertical blade
)


def variant_cell_id(tile_id: str, index: int) -> str:
    """Atlas cell id for variant `index` (1-based) of `tile_id`.

    The single naming convention shared by `expand_terrain_variants` (cell
    generation) and `build_variant_layout` (field placement), so a spec's
    variants pack and place identically through every path.
    """
    return f"{tile_id}.v{index}"


def dominant_interior_color(canvas: Canvas) -> tuple[int, int, int] | None:
    """The most common opaque interior pixel's RGB (1px border ring excluded) —
    a terrain tile's flat fill. `None` for tiles with no opaque interior.

    After the polish pass this pixel already *is* a palette colour, so
    `palette.nearest` on it is identity (the tone-matched-ring trick).
    """
    arr = canvas.array
    h, w = arr.shape[:2]
    if h < 3 or w < 3:
        return None
    interior = arr[1 : h - 1, 1 : w - 1]
    opaque = interior[..., 3] != 0
    if not opaque.any():
        return None
    rgb = interior[opaque][:, :3].reshape(-1, 3)
    uniq, counts = np.unique(rgb, axis=0, return_counts=True)
    top = uniq[int(np.argmax(counts))]
    return (int(top[0]), int(top[1]), int(top[2]))


def _interior_skeleton(
    base_tile: Canvas, palette: ResolvedPalette
) -> tuple[tuple[int, int, int], list[tuple[int, int]], list[tuple[int, int]]]:
    """`(fill, skeleton, authored)` for a terrain tile's interior.

    - `fill`: the dominant interior colour (the flat fill).
    - `skeleton`: every interior pixel whose colour is not the fill — the
      authored speckles *and* any interior-shade ramp tones (`_light`/`_shadow`
      clusters from `shade_terrain_interior`). Re-scatter erases the whole
      skeleton so no cell shares the base's detail at identical offsets.
    - `authored`: the skeleton subset that is not a ramp tone of the fill's
      palette colour — the authored speckles proper, which re-scatter
      re-places at seeded positions (same count, new offsets).
    """
    fill = dominant_interior_color(base_tile)
    if fill is None:
        return (0, 0, 0), [], []
    fill_rgba = (fill[0], fill[1], fill[2], 255)
    color_id = palette.nearest((*fill, 255))
    ramp_tone_ids = [
        tone_id
        for suffix in ("shadow", "dark", "deep", "light", "bright", "glow")
        if (tone_id := f"{color_id}_{suffix}") in palette.ids
    ]
    ramp_tones = {palette.rgba(tone_id) for tone_id in ramp_tone_ids}
    arr = base_tile.array
    h, w = arr.shape[:2]
    skeleton: list[tuple[int, int]] = []
    authored: list[tuple[int, int]] = []
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            px = tuple(arr[y, x])
            if px[3] == 0 or px == fill_rgba:
                continue
            skeleton.append((x, y))
            if px not in ramp_tones:
                authored.append((x, y))
    return fill, skeleton, authored


def shade_terrain_interior(
    canvas: Canvas,
    palette: ResolvedPalette,
    *,
    seed: int = 0,
    clusters: int = 6,
    cluster_size: int = 3,
) -> Canvas:
    """Deterministically shade a terrain tile's flat fill with its own
    material's ramp tones (the dominant colour's `_light`/`_shadow` palette
    steps), in organic 2-4px clusters scattered across the interior — the
    reference's dense tone-cluster ground instead of a value-flat fill.

    Only pixels whose colour is exactly the tile's dominant interior colour
    (the flat fill) are painted, so authored detail, transition patches and
    the 1px border ring are never touched: self-seams stay zero and every
    painted pixel is a palette colour by construction. Tiles whose dominant
    colour has no ramp tones in `palette` (a flat palette) are returned
    unchanged. Pure function of canvas + palette + seed — deterministic.
    """
    out = canvas.copy()
    fill = dominant_interior_color(out)
    if fill is None:
        return out
    color_id = palette.nearest((*fill, 255))
    tone_ids = [
        tone_id
        for suffix in ("light", "shadow")
        if (tone_id := f"{color_id}_{suffix}") in palette.ids
    ]
    tones = [palette.rgba(tone_id) for tone_id in tone_ids]
    if not tones:
        return out
    arr = out.array
    h, w = arr.shape[:2]
    if h < 5 or w < 5:
        return out
    fill_rgb = np.array(fill, dtype=np.uint8)
    # Anchor pool: fill pixels at least 1px from the ring, so no shade pixel
    # (max shape offset 1) can touch the border ring.
    candidates = [
        (x, y)
        for y in range(1, h - 3)
        for x in range(1, w - 3)
        if np.array_equal(arr[y, x, :3], fill_rgb)
    ]
    if not candidates:
        return out
    rng = random.Random(seed)
    rng.shuffle(candidates)
    painted = 0
    for c in range(clusters):
        if c >= len(candidates):
            break
        ax, ay = candidates[c]
        shape = _TUFT_SHAPES[rng.randrange(len(_TUFT_SHAPES))]
        count = 0
        for dx, dy in shape:
            if count >= cluster_size:
                break
            px, py = ax + dx, ay + dy
            if not (1 <= px < w - 1 and 1 <= py < h - 1):
                continue
            if not np.array_equal(arr[py, px, :3], fill_rgb):
                continue  # only the flat fill, never authored detail
            tone = tones[painted % len(tones)]
            arr[py, px, 0] = tone[0]
            arr[py, px, 1] = tone[1]
            arr[py, px, 2] = tone[2]
            painted += 1
            count += 1
    return out


def build_variation_tiles(
    base_tile: Canvas,
    palette: ResolvedPalette,
    variations: int,
    *,
    seed: int = 0,
    detail_colors: Sequence[RGBA] | None = None,
    pixels_per_variant: int | None = None,
    clusters: int = 2,
    cluster_size: int = 4,
    shade: bool = True,
) -> list[Canvas]:
    """Deterministically generate `variations` interior variants of `base_tile`.

    Three modes:

    - **Full re-scatter** (default, `pixels_per_variant=None`): each variant
      *displaces* the base tile's authored interior detail instead of only
      adding tufts on top of it — it erases the base's interior skeleton
      (authored speckles and any interior-shade ramp-tone clusters), then
      paints its own tuft clusters and re-scatters the authored speckle count
      at seeded positions, and finally re-shades its flat fill with the
      material's ramp tones under its own seed. No 16px-period detail pattern
      survives from the base: a field of one base tile plus N-1 variants reads
      as continuous textured ground, not a copy-pasted lattice. A variant
      differs from its base by roughly 5-15% of the interior (well above the
      perceptual threshold the round-3 4-8px tufts fell under).
    - **Cluster mode** (`pixels_per_variant=None` with `shade=False` is not a
      separate mode; pass `clusters`/`cluster_size` to tune the tufts): kept
      for callers that want add-only tufts with no skeleton displacement.
    - **Legacy scatter mode** (`pixels_per_variant` given): scatters exactly
      that many single pixels, as shipped — byte-identical for callers that
      rely on the exact pixel count.

    The 1px border ring is never touched, so every variant's N/S/E/W edges
    stay byte-identical to the base and the tile keeps its seam guarantees
    (`check_seams` against the base is all zeros). `detail_colors` defaults to
    the palette's `light`/`shadow` role colours — falling back to the base
    tile's OWN material's ramp tones (dark/light steps of its dominant
    interior colour) when the palette has no role colours — so variations read
    as highlight/shadow accents of the tile's own material rather than random
    noise or a foreign palette swap. Callers pair this with
    `TileSpec.variations` (the spec's declarative variant count) after
    `render_terrain_tiles`; the atlas packing is a caller concern (variants
    are extra cells, not new `doc.tiles` ids).

    Deterministic: anchors and shapes are drawn from `random.Random(seed)`
    (Mersenne Twister, fixed across platforms) and the per-variant re-shade
    seeds are derived from `seed`, so the same arguments always return
    byte-identical canvases. Two different base tiles should be given
    different seeds (e.g. a stable hash of the tile id) or they will share one
    scatter pattern.

    Raises `ForgeError` when `variations` x painted-pixels cannot fit
    pairwise-disjoint pixel sets in the interior, or when two variants (or a
    variant and the base) come out pixel-identical — a degenerate palette whose
    detail colours equal the pixels they overwrite.
    """
    if variations < 1:
        raise ForgeError(f"build_variation_tiles: variations must be >= 1, got {variations}")
    if pixels_per_variant is not None and pixels_per_variant < 1:
        raise ForgeError(
            f"build_variation_tiles: pixels_per_variant must be >= 1, got {pixels_per_variant}"
        )
    if clusters < 1:
        raise ForgeError(f"build_variation_tiles: clusters must be >= 1, got {clusters}")
    if cluster_size < 1:
        raise ForgeError(f"build_variation_tiles: cluster_size must be >= 1, got {cluster_size}")
    w, h = base_tile.width, base_tile.height
    interior_w, interior_h = w - 2, h - 2
    if interior_w < 1 or interior_h < 1:
        raise ForgeError(
            f"build_variation_tiles: {w}x{h} tile has no interior to vary (need >= 3x3)"
        )
    interior_count = interior_w * interior_h
    if pixels_per_variant is not None:
        pixels_per = pixels_per_variant
    else:
        _, _skeleton, authored = _interior_skeleton(base_tile, palette)
        pixels_per = clusters * cluster_size + len(authored)
    if variations * pixels_per > interior_count:
        raise ForgeError(
            f"build_variation_tiles: {variations} variants x {pixels_per} pixels "
            f"do not fit the {w}x{h} tile's {interior_count}-pixel interior"
        )
    colors = (
        _variation_detail_colors(palette, base_tile)
        if detail_colors is None
        else list(detail_colors)
    )
    if not colors:
        raise ForgeError("build_variation_tiles: no detail colours available")

    rng = random.Random(seed)
    if pixels_per_variant is not None:
        variants = _scatter_variants(base_tile, colors, variations, pixels_per_variant, rng)
    else:
        if w < 5 or h < 5:
            raise ForgeError(
                f"build_variation_tiles: {w}x{h} tile's interior is too small for tuft "
                "clusters — pass pixels_per_variant for single-pixel scatter"
            )
        variants = _rescatter_variants(
            base_tile, colors, variations, clusters, cluster_size, rng, palette, seed, shade
        )

    for i, variant in enumerate(variants):
        for other in [base_tile, *variants[:i], *variants[i + 1 :]]:
            if np.array_equal(variant.array, other.array):
                raise ForgeError(
                    f"build_variation_tiles: variant #{i} is pixel-identical to another "
                    "tile — detail colours match the pixels they overwrite; pick detail "
                    "colours that differ from the tile's interior"
                )
    return variants


def _scatter_variants(
    base_tile: Canvas,
    colors: Sequence[RGBA],
    variations: int,
    pixels_per_variant: int,
    rng: random.Random,
) -> list[Canvas]:
    """Legacy single-pixel scatter: `pixels_per_variant` seeded pixels per
    variant, evenly strided over a shuffled interior pool (unchanged from the
    original behaviour, byte-for-byte)."""
    w, h = base_tile.width, base_tile.height
    interior_count = (w - 2) * (h - 2)
    pool = [(x, y) for y in range(1, h - 1) for x in range(1, w - 1)]
    rng.shuffle(pool)
    stride = interior_count // variations
    variants: list[Canvas] = []
    for i in range(variations):
        variant = base_tile.copy()
        for j, (x, y) in enumerate(pool[i * stride : i * stride + pixels_per_variant]):
            variant.set_pixel(x, y, colors[(i + j) % len(colors)])
        variants.append(variant)
    return variants


def _rescatter_variants(
    base_tile: Canvas,
    colors: Sequence[RGBA],
    variations: int,
    clusters: int,
    cluster_size: int,
    rng: random.Random,
    palette: ResolvedPalette,
    seed: int,
    shade: bool,
) -> list[Canvas]:
    """Full-interior re-scatter (the round-4 lattice fix): each variant
    *displaces* the base's authored detail instead of only adding tufts.

    Every variant starts from the base, erases the base's interior skeleton
    (authored speckles AND the base's interior-shade tone clusters — so no
    cell shares the base's detail skeleton at identical offsets), then paints
    its own tuft clusters and re-scatters the authored speckle count at seeded
    positions, and finally re-shades its flat fill with the material's ramp
    tones under its own seed. The 1px border ring is never touched, so every
    variant keeps the base tile's seams byte-for-byte.

    Deterministic: anchors and speckle positions come from two pools shuffled
    once with `rng` (a fixed `random.Random(seed)` sequence) and handed to
    variants in disjoint slices, so painted pixels never overlap between
    variants; the per-variant re-shade seeds are derived from `seed`.
    """
    fill, skeleton, authored = _interior_skeleton(base_tile, palette)
    fill_rgba = (fill[0], fill[1], fill[2], 255)
    w, h = base_tile.width, base_tile.height
    # Max tuft shape offset is 1, so tuft anchors must stay in [1, w-3] x
    # [1, h-3] to keep every cluster pixel strictly interior.
    anchors = [(x, y) for y in range(1, h - 3) for x in range(1, w - 3)]
    speckle_pool = [(x, y) for y in range(1, h - 1) for x in range(1, w - 1)]
    rng.shuffle(anchors)
    rng.shuffle(speckle_pool)
    used: set[tuple[int, int]] = set()
    variants: list[Canvas] = []
    for i in range(variations):
        variant = base_tile.copy()
        # 1. Erase the base's skeleton: authored speckles and the base's shade
        #    tone clusters go back to the flat fill, so the base's detail
        #    offsets never repeat at a 16px period.
        for x, y in skeleton:
            variant.set_pixel(x, y, fill_rgba)
        painted = 0
        # 2. Organic tuft clusters at seeded anchors.
        for c in range(clusters):
            anchor_index = i * clusters + c
            if anchor_index >= len(anchors):
                break
            ax, ay = anchors[anchor_index]
            shape = _TUFT_SHAPES[rng.randrange(len(_TUFT_SHAPES))]
            cluster_painted = 0
            for dx, dy in shape:
                if cluster_painted >= cluster_size:
                    break
                px, py = ax + dx, ay + dy
                if (px, py) in used:
                    continue
                variant.set_pixel(px, py, colors[(i + painted) % len(colors)])
                used.add((px, py))
                painted += 1
                cluster_painted += 1
        # 3. Re-scatter the authored speckle count at new seeded positions —
        #    the same number of isolated detail pixels, displaced.
        for j in range(len(authored)):
            pool_index = i * len(authored) + j
            if pool_index >= len(speckle_pool):
                break
            px, py = speckle_pool[pool_index]
            if (px, py) in used:
                continue
            variant.set_pixel(px, py, colors[(i + painted) % len(colors)])
            used.add((px, py))
            painted += 1
        # 4. Per-variant interior shade (own seed) so no cell shares a tone
        #    skeleton either.
        if shade:
            shade_seed = zlib.crc32(f"{seed}:shade:{i}".encode())
            variant = shade_terrain_interior(variant, palette, seed=shade_seed)
        variants.append(variant)
    return variants


def _cell_variant_hash(tile_id: str, x: int, y: int, seed: int, count: int) -> int:
    """Deterministic per-cell variant hash in `[0, count)`.

    sha256 of the (tile id, x, y, seed) tuple — a proper avalanche mixer. A
    CRC32 of a short coordinate string is *linear* over GF(2): two messages
    differing by one byte differ by a fixed XOR pattern, so `crc32` mod 4 of
    consecutive coordinates lands on a 2-periodic wallpaper (measured: every
    other cell repeated exactly). sha256 has no such structure.
    """
    digest = hashlib.sha256(f"{tile_id}:{x}:{y}:{seed}".encode()).hexdigest()
    return int(digest[:8], 16) % count


# A cell's variant must differ from its orthogonally-adjacent neighbours (the
# round-5 no-adjacent-repeat rule) AND from the cells two away along rows and
# columns — the round-6 checkerboard fix. Without the distance-2 terms, the
# same variant survives every 2nd cell (measured row
# `[v1, base, v1, v2, v1, v2, v3, base]` -> grass.v1 at cols 0,2,4 = a 32px
# checker of pixel-identical detail in sparse views), which the 16px/48px
# autocorrelation bars do NOT catch (offset-2 AC was 0.222-0.333).
_LAYOUT_BUMP_OFFSETS: tuple[tuple[int, int], ...] = ((1, 0), (2, 0), (0, 1), (0, 2))

# Deterministic dead-end repair window (see `_repair_variant_dead_end`):
# 4 rows up x 13 columns around the cell, biased right by 2 so the repair can
# also re-colour the already-placed cells the chain knocks on.
_REPAIR_ROWS_UP = 4
_REPAIR_COLS_LEFT = 10
_REPAIR_COLS_RIGHT = 2
_REPAIR_NODE_BUDGET = 5_000_000


def _repair_variant_dead_end(
    values: list[list[int]],
    out: list[list[str]],
    rows: Sequence[Sequence[str]],
    x: int,
    y: int,
    tile_id: str,
    count: int,
    seed: int,
) -> bool:
    """Deterministically repair a variant-layout dead end by re-colouring a
    bounded window around the cell.

    The four bump constraints (left, left2, top, top2) can together forbid
    *every* variant — a dead end the round-5 two-neighbour bump could never
    hit (two forbidden neighbours never exhaust count >= 3). The constraint
    chain that creates a dead end is local, so a bounded backtracking search
    over the window (rows `y-4..y`, cols `x-10..x+2`, same-tile cells only)
    finds a valid re-colouring almost always; the search is a pure function
    of the already-placed grid and the seed, so the repair is deterministic.
    Cells of other tiles are never touched (their ids can never equal this
    tile's candidates anyway).

    Returns True with `values`/`out` updated when a valid assignment was
    found; otherwise restores the grid and returns False, leaving the caller
    to its deterministic least-conflict fallback (measured unreachable in
    practice: 0 failures over ~2.2M cells across 8x8 and 16x16 fields).
    """
    height = len(values)
    width = len(values[0])
    y_lo = max(0, y - _REPAIR_ROWS_UP)
    x_lo = max(0, x - _REPAIR_COLS_LEFT)
    x_hi = min(width - 1, x + _REPAIR_COLS_RIGHT)
    cells = [
        (cx, cy)
        for cy in range(y_lo, y + 1)
        for cx in range(x_lo, x_hi + 1)
        if rows[cy][cx] == tile_id
    ]
    in_window = set(cells)
    old = {cell: values[cell[1]][cell[0]] for cell in cells}

    def _conflicts_fixed(cx: int, cy: int, value: int) -> bool:
        """True when `value` equals a same-tile cell outside the window."""
        for dx, dy in _LAYOUT_BUMP_OFFSETS:
            for sx, sy in ((1, 1), (-1, -1)):
                nx, ny = cx + sx * dx, cy + sy * dy
                if not (0 <= nx < width and 0 <= ny < height):
                    continue
                if (nx, ny) in in_window:
                    continue
                if rows[ny][nx] == tile_id and values[ny][nx] == value:
                    return True
        return False

    allowed: dict[tuple[int, int], list[int]] = {}
    for cell in cells:
        chosen = _cell_variant_hash(tile_id, cell[0], cell[1], seed, count)
        vals = [
            (chosen + k) % count
            for k in range(count)
            if not _conflicts_fixed(cell[0], cell[1], (chosen + k) % count)
        ]
        # Keep each cell's current value first: the found assignment is the
        # minimal-disruption one, so the repaired layout stays hash-driven.
        vals.sort(key=lambda v: v != old[cell])
        allowed[cell] = vals
    order = sorted(cells, key=lambda cell: (len(allowed[cell]), cell[1], cell[0]))

    nodes = 0
    assigned: set[tuple[int, int]] = set()

    def _search(i: int) -> bool:
        nonlocal nodes
        nodes += 1
        if nodes > _REPAIR_NODE_BUDGET:
            return False
        if i == len(order):
            return True
        cx, cy = order[i]
        for value in allowed[(cx, cy)]:
            conflict = False
            for dx, dy in _LAYOUT_BUMP_OFFSETS:
                for sx, sy in ((1, 1), (-1, -1)):
                    nx, ny = cx + sx * dx, cy + sy * dy
                    if (nx, ny) in in_window and (nx, ny) in assigned and values[ny][nx] == value:
                        conflict = True
                        break
                if conflict:
                    break
            if conflict:
                continue
            values[cy][cx] = value
            assigned.add((cx, cy))
            if _search(i + 1):
                return True
            assigned.discard((cx, cy))
            values[cy][cx] = old[(cx, cy)]
        return False

    if not _search(0):
        for cell in cells:
            values[cell[1]][cell[0]] = old[cell]
        return False
    for cx, cy in cells:
        if values[cy][cx] != old[(cx, cy)]:
            value = values[cy][cx]
            out[cy][cx] = tile_id if value == 0 else variant_cell_id(tile_id, value)
    return True


def build_variant_layout(
    rows: Sequence[Sequence[str]],
    variations: Mapping[str, int],
    *,
    seed: int = 0,
) -> list[list[str]]:
    """Per-cell seeded variant assignment for an assembled terrain scene.

    Cells whose tile declares more than one variation are assigned a variant
    index in `[0, variations)` from `_cell_variant_hash` — never a linear form
    `(a*x + b*y) % n`, which degenerates into an exact wallpaper whenever n
    divides a or b (the round-3 demo layout `(x*7 + y*3) % 3` dropped the
    y-term entirely and repeated the same variant down every column). The
    hash's candidate is bumped through a fixed deterministic sequence until it
    differs from the cell's left, left-2, top and top-2 neighbours, so no two
    cells within Chebyshev distance 2 *along rows/columns* ever carry the
    same pattern — neither a 16px-period content lattice (orthogonal
    neighbours) nor an every-2nd-cell checker of identical detail (the round-6
    gap: `[v1, base, v1, ...]` at 32px, invisible to the 16/48px AC bars) can
    survive in the field. (The *full* Chebyshev-2 neighbourhood is not
    enforceable with 4 variants: cells (0,0), (2,0), (0,2), (2,2), (1,1) are
    pairwise within distance 2, a 5-clique.)

    The four forbidden cells can exhaust every variant (a dead end — the
    round-5 two-neighbour bump could never hit one). Dead ends are resolved
    by a deterministic bounded backtracking repair
    (`_repair_variant_dead_end`), and the effectively-unreachable residual
    falls back to the deterministic least-conflicting candidate, so the
    result is a pure function of `rows`, `variations` and `seed`: same seed,
    byte-identical layout.

    Returns rows with base ids replaced by `variant_cell_id(tile_id, i)`
    (i >= 1) where a variant was chosen; cells whose tile declares
    `variations <= 1` keep their id unchanged.
    """
    height = len(rows)
    width = len(rows[0]) if height else 0
    out: list[list[str]] = [[""] * width for _ in range(height)]
    values: list[list[int]] = [[-1] * width for _ in range(height)]
    for y in range(height):
        for x in range(width):
            tile_id = rows[y][x]
            count = max(1, variations.get(tile_id, 1))
            if count <= 1:
                values[y][x] = 0
                out[y][x] = tile_id
                continue
            chosen = _cell_variant_hash(tile_id, x, y, seed, count)
            forbidden: set[int] = set()
            for dx, dy in _LAYOUT_BUMP_OFFSETS:
                nx, ny = x - dx, y - dy
                if nx >= 0 and ny >= 0 and rows[ny][nx] == tile_id and values[ny][nx] >= 0:
                    forbidden.add(values[ny][nx])
            for k in range(count):
                candidate = (chosen + k) % count
                if candidate not in forbidden:
                    values[y][x] = candidate
                    out[y][x] = tile_id if candidate == 0 else variant_cell_id(tile_id, candidate)
                    break
            else:
                if _repair_variant_dead_end(values, out, rows, x, y, tile_id, count, seed):
                    continue
                # Deterministic least-conflict fallback (see docstring): the
                # candidate that violates the fewest bump constraints, ties
                # broken by probe order.
                best = chosen
                best_conflicts = count
                for k in range(count):
                    candidate = (chosen + k) % count
                    conflicts = sum(1 for f in forbidden if f == candidate)
                    if conflicts < best_conflicts:
                        best_conflicts = conflicts
                        best = candidate
                values[y][x] = best
                out[y][x] = tile_id if best == 0 else variant_cell_id(tile_id, best)
    return out
