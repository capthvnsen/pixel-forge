# `forest_tileset.yaml` — terrain example

A 16x16 grass/dirt/water tileset. Exists to prove out the `asset.type: terrain`
schema surface end to end: base tiles, a full 8-mask transition set, animated
water, terrain sets, per-tile collision/navigation metadata, and a sample map.

## What it demonstrates

- **Base tiles**: `grass` and `dirt`, each a `TileSpec` built from three
  layered `regions` (an edge-ring rect, an interior fill rect, and a handful
  of interior `pixel` shapes for texture).
- **Full transition coverage**: 8 `grass_dirt_*` tiles, one per `mask` code
  (`N`, `NE`, `E`, `SE`, `S`, `SW`, `W`, `NW`), each declared as a
  `TransitionSpec` from `grass` to `dirt`. Every terrain pair present
  (`grass`↔`dirt`) has at least one transition tile (`TIL001`).
- **Animated water**: `animated_tiles.water` cycles `water_1` → `water_2` →
  `water_3` (a shimmer highlight sweeping the interior) at 300ms/frame.
- **Terrain sets**: `terrain_sets.grass` / `terrain_sets.dirt` declare which
  tile ids belong to each terrain for Godot's peering-bit autotile logic.
- **Per-tile metadata**: water is `collision: solid`, `navigation: false`;
  grass/dirt/transitions are all `navigation: true` with no collision.
- **Sample map**: an 8x8 `sample_map` — three rows of grass, a 3-row dirt
  path (grass-edge / plain-dirt / grass-edge) running east–west, more grass,
  and a 2x2 pond.

## The seam design (the hard requirement)

`validation.max_seam_mismatch: 0` is left at its strict default — every tile
must tile against itself with **zero** mismatched edge pixels, and the task
was to earn that by design, not by loosening the threshold.

Every tile here is built as a 1px `tile_edge` "grout" ring covering the whole
16x16 tile (`layer: 0`), with the tile's real content (grass, dirt, the
grass-encroachment patch, or the water shimmer) painted only into the
interior 14x14 block at `[1,1]`–`[14,14]` (`layer: 1+`). Row 0, row 15,
column 0, and column 15 are *never* touched by anything but the edge-ring
shape. That means:

- Every tile's N/S/E/W border is the same 16 identical `tile_edge` pixels.
- **Self-pairs** (a tile against itself) trivially match — `TIL003`'s hard
  requirement.
- **Cross-pairs** (any tile against any other) also trivially match, since
  every tile shares the identical border colour, which is a bonus, not a
  requirement — it just means the tileset reads as having a deliberate
  grout/outline style rather than needing a warning-generating discontinuity
  explained away.

`tests/end_to_end/test_terrain_example.py::test_every_tile_self_tiles_with_zero_seam_mismatch`
asserts this directly against `check_seams`.

## Godot mapping

- `tiles` → a `TileSetAtlasSource` (one atlas cell per static tile id, packed
  via `build_atlas`).
- `terrain_sets` → Godot 4 `TileSet` terrains (`add_terrain_set`,
  `add_terrain`), `mode: corners_and_edges` → `TileSet.TERRAIN_MODE_CORNERS_AND_SIDES`.
- `transitions` (`mask` codes) → per-tile peering-bit configuration
  (`tile_set_terrain_peering_bit`) so Godot's autotile painter picks the
  right transition tile at each `mask` position.
- `animated_tiles.water` → a Godot `TileData` animation (`animation_frames_count`,
  `animation_separation`, `animation_speed`) on the water atlas cell, or an
  `AnimatedTexture` if exported as a standalone sprite.
- `collision` / `navigation` / `occlusion` → per-tile `TileData` physics,
  navigation, and occlusion layers.
- `sample_map` → a reference `TileMapLayer` painted with the exported atlas,
  useful for visually sanity-checking the import.

## Validation

`report.blocking is False`, and in fact the report has **zero findings at
all** — no errors, no warnings, no info notes. `TIL007`'s repeat-ratio check
(`max_repeat_ratio: 0.6`) comes closest: `grass` covers 36/64 = 56.25% of the
sample map, under the 60% default, so it doesn't fire.
