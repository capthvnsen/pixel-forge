"""Neutral `TileSet` payload for terrain assets."""

from __future__ import annotations

from collections.abc import Mapping

from pixel_forge.errors import ExportError
from pixel_forge.rendering.sheet import SheetCell
from pixel_forge.schemas.asset import TerrainAsset
from pixel_forge.schemas.common import Vec2
from pixel_forge.schemas.manifest import (
    GodotAnimatedTileExport,
    GodotSampleMapExport,
    GodotTerrainSetExport,
    GodotTileCoord,
    GodotTileSetExport,
)

# Godot 4 `TileData` terrain peering-bit names for a square-grid `TileSet`, keyed
# by the edge/corner code used in `TransitionSpec.mask` ("N", "NE", "E", ...).
PEERING_BIT_NAMES: dict[str, str] = {
    "N": "top_side",
    "NE": "top_right_corner",
    "E": "right_side",
    "SE": "bottom_right_corner",
    "S": "bottom_side",
    "SW": "bottom_left_corner",
    "W": "left_side",
    "NW": "top_left_corner",
}


def peering_bit_name(mask: str) -> str:
    try:
        return PEERING_BIT_NAMES[mask]
    except KeyError:
        raise ExportError(f"unknown terrain transition mask: {mask!r}") from None


def _coord(atlas_cells: Mapping[str, SheetCell], tile_id: str, tw: int, th: int) -> Vec2:
    cell = atlas_cells.get(tile_id)
    if cell is None:
        raise ExportError(f"unknown tile id: {tile_id!r}")
    if cell.x % tw != 0 or cell.y % th != 0:
        raise ExportError(
            f"tile {tile_id!r} at ({cell.x}, {cell.y}) is not aligned to tile size {tw}x{th}"
        )
    return (cell.x // tw, cell.y // th)


def build_tileset(
    doc: TerrainAsset,
    atlas_cells: Mapping[str, SheetCell],
    texture_path: str,
) -> GodotTileSetExport:
    """`atlas_coords` are the cell's pixel x/y divided by the shared tile size;
    a cell not aligned to that grid raises `ExportError`.
    """
    if not atlas_cells:
        raise ExportError("build_tileset: atlas_cells must be non-empty")

    sizes = {(cell.w, cell.h) for cell in atlas_cells.values()}
    if len(sizes) > 1:
        raise ExportError(f"build_tileset: atlas cells must share one tile size, got {sizes!r}")
    tw, th = next(iter(sizes))
    tile_size: Vec2 = (tw, th)

    tiles = []
    for tile_id in sorted(atlas_cells):
        x, y = _coord(atlas_cells, tile_id, tw, th)
        tiles.append(GodotTileCoord(tile_id=tile_id, x=x, y=y))

    terrain_sets = {
        name: GodotTerrainSetExport(mode=ts.mode, tiles=list(ts.tiles))
        for name, ts in doc.terrain_sets.items()
    }

    # tile id -> peering-bit name -> terrain name, pre-resolved from `transitions` so the
    # plugin never reimplements the mask table. `to_terrain` is the terrain encroaching
    # along that edge/corner of the tile (the "other side" of the transition).
    terrain_bits: dict[str, dict[str, str]] = {}
    for transition in doc.transitions:
        terrain_bits.setdefault(transition.tile_id, {})[peering_bit_name(transition.mask)] = (
            transition.to_terrain
        )

    animated_tiles = {}
    for name, spec in doc.animated_tiles.items():
        frames = []
        for tile_id in spec.frames:
            x, y = _coord(atlas_cells, tile_id, tw, th)
            frames.append(GodotTileCoord(tile_id=tile_id, x=x, y=y))
        animated_tiles[name] = GodotAnimatedTileExport(
            frames=frames, frame_duration_ms=spec.frame_duration_ms, loop=spec.loop
        )

    sample_map = None
    if doc.sample_map is not None:
        sample_map = GodotSampleMapExport(
            size=doc.sample_map.size,
            layers={
                layer_name: [
                    [_coord(atlas_cells, tile_id, tw, th) for tile_id in row] for row in rows
                ]
                for layer_name, rows in doc.sample_map.layers.items()
            },
        )

    collision_tiles = sorted(tid for tid, tile in doc.tiles.items() if tile.collision is not None)
    navigation_tiles = sorted(tid for tid, tile in doc.tiles.items() if tile.navigation)
    occlusion_tiles = sorted(tid for tid, tile in doc.tiles.items() if tile.occlusion)

    return GodotTileSetExport(
        atlas_source=texture_path,
        tile_size=tile_size,
        tiles=tiles,
        terrain_sets=terrain_sets,
        transitions=list(doc.transitions),
        terrain_bits=terrain_bits,
        animated_tiles=animated_tiles,
        sample_map=sample_map,
        collision_tiles=collision_tiles,
        navigation_tiles=navigation_tiles,
        occlusion_tiles=occlusion_tiles,
    )
