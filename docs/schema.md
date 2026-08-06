# Asset schema reference

Every model below is a pydantic model with `model_config = ConfigDict(extra="forbid")`
unless noted otherwise: an unrecognized field in a YAML spec is a validation error, not
a silent ignore. Source: `src/pixel_forge/schemas/`. Types use the Python spelling
(`Vec2 = tuple[int, int]`, `RGBA = tuple[int, int, int, int]`).

An asset's on-disk YAML never contains a `kind` field — `kind` is injected by
`parse_asset_doc` (from `asset.type`) purely so pydantic's discriminated union can pick
the right model (see `docs/adr/0001-architecture.md` for why). Hand-authored specs
omit it entirely.

## Top-level document: `AssetDoc`

`AssetDoc = CharacterAsset | EnemyAsset | PropAsset | TerrainAsset`, discriminated on
`kind`. `CharacterAsset`, `EnemyAsset`, and `PropAsset` all extend `SpriteAssetBase`
(itself extending `BaseAssetDoc`); `TerrainAsset` extends `BaseAssetDoc` directly and
has a completely different body (tiles, not regions/animations).

### `BaseAssetDoc` — fields shared by all four asset kinds

| Field | Type | Default | Meaning |
|---|---|---|---|
| `schema_version` | `Literal[1]` | required | Must be `1`. `parse_asset_doc` rejects anything else before even looking at `asset.type`. |
| `kind` | `AssetType` | derived | Mirrors `asset.type`; never hand-authored (see above). A model validator rejects a document where the two disagree. |
| `asset` | `AssetHeader` | required | Identity, canvas size, perspective, pixel-scale, baseline. |
| `palette` | `PaletteRef` (= `Palette`) | required | The full palette this asset's shapes draw from. |
| `export` | `ExportOptions` | required | Sheet/preview/Godot export knobs. |
| `validation` | `ValidationOptions` | required | Per-asset thresholds the validation rules read. |

`AssetType = Literal["character", "enemy", "prop", "terrain"]`.

### `AssetHeader`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `id` | `str` | required | The asset id; must match the directory/file name (`assets/<id>/<id>.yaml`) and pass `validate_asset_id` (`^[a-z0-9][a-z0-9_]*$`, max 64 chars). |
| `type` | `AssetType` | required | `character`, `enemy`, `prop`, or `terrain`. |
| `canvas` | `Vec2` | required | Pixel width/height every rendered sprite frame must exactly match (`PIX001`). Not used by terrain (tiles have their own `TileSpec.size`). |
| `perspective` | `str` | `"three_quarter_top_down"` | Free-text descriptive label; not read by any rule or renderer. |
| `logical_pixel_scale` | `int` | `1` | Declares the intended "chunky pixel" size; `PIX009` checks every opaque feature is aligned to a grid of this size. |
| `baseline_y` | `int \| None` | `None` | The canvas row every rendered frame's lowest opaque pixel must land on. `None` skips `ANI001` (baseline-drift) entirely. |

### `ExportOptions`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `sheet_columns` | `int \| None` | `None` | Columns per row when packing the sprite sheet. `None` uses the widest (animation, direction) group's frame count (`build_sprite_sheet`). |
| `preview_format` | `Literal["gif", "webp"]` | `"gif"` | Default format for `generate_preview` when the CLI/MCP caller doesn't override it. |
| `preview_loop` | `bool` | `True` | Passed through to `write_preview`'s `loop` for the produced GIF/WebP. |
| `godot` | `bool` | `True` | Declared intent to export to Godot; not currently read as a gate anywhere in `api.py` — `export_godot`/`build_asset` always attempt the export regardless of this flag (documented here as-is; not verified to affect control flow). |

### `ValidationOptions`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `palette_limit` | `int` | `24` | Max colours a palette may declare (`PIX005`). |
| `require_stable_baseline` | `bool` | `True` | Gates `ANI001`. Set `False` to skip baseline-drift checking. |
| `require_stable_anchors` | `bool` | `True` | Gates `ANI002`. Set `False` to skip foot/body-anchor drift checking. |
| `allow_antialiasing` | `bool` | `False` | Gates `PIX003` (off-palette colour = AA artifact). Set `True` if a render backend intentionally produces blended edges. |
| `max_seam_mismatch` | `int` | `0` | Terrain only. Mismatched edge pixels tolerated before `TIL003`/`TIL004` fire. |
| `max_repeat_ratio` | `float` | `0.6` | Terrain only. Share of a `sample_map` layer one tile id may occupy before `TIL007` (heuristic) fires. |

### `SpriteAssetBase` — shared by `character`/`enemy`/`prop`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `directions` | `list[str]` | required | Named directions this asset renders, e.g. `["south", "west", "east", "north"]`. Free-text — no fixed enum. |
| `mirror` | `dict[str, str]` | `{}` | `dst -> src`: `dst` is never hand-authored; it's produced by flipping `src`'s rendered frames (`Canvas.mirror_x`). A mirror source cannot itself be a mirror target (no chains). |
| `anchors` | `dict[str, Vec2]` | required | Named world-space points, e.g. `feet`, `torso`. Every `Region.anchor` must name one of these. |
| `regions` | `dict[str, Region]` | required | The asset's drawable layers. |
| `direction_overrides` | `dict[str, dict[str, RegionTransform]]` | `{}` | `direction -> region -> transform`, applied as a base layer under every frame's own per-frame transform for that direction. |
| `animations` | `dict[str, AnimationSpec]` | required | Named animations; must be non-empty. |

`CharacterAsset` and `EnemyAsset` add nothing beyond `kind`, except `EnemyAsset` also
carries `combat`. `PropAsset` adds `moving_regions`/`procedural_regions`.

### `EnemyAsset` extra field

| Field | Type | Default | Meaning |
|---|---|---|---|
| `combat` | `EnemyCombat` | required | Combat metadata; see below. |

**`EnemyCombat`**

| Field | Type | Default | Meaning |
|---|---|---|---|
| `telegraph_animation` | `str \| None` | `None` | Name of the animation that telegraphs an attack. Not cross-checked against `animations` by any current validator. |
| `death_animation` | `str \| None` | `None` | Name of the death animation. Same caveat. |
| `hit_frames` | `dict[str, list[int]]` | `{}` | `animation -> [frame indices]` where a hit registers. |

### `PropAsset` extra fields

| Field | Type | Default | Meaning |
|---|---|---|---|
| `moving_regions` | `list[str]` | `[]` | Region names driven by per-frame `RegionTransform.offset` (documentation/exporter hint; see `docs/godot.md`'s beacon mapping). |
| `procedural_regions` | `list[str]` | `[]` | Region names whose animation is expressed via `AnimationSpec.procedural` rather than baked per-frame transforms. |

### `TerrainAsset` — a wholly different body

| Field | Type | Default | Meaning |
|---|---|---|---|
| `tiles` | `dict[str, TileSpec]` | required | Every tile id this terrain declares. |
| `terrain_sets` | `dict[str, TerrainSet]` | `{}` | Named groups of tile ids for Godot's terrain autotiling. |
| `transitions` | `list[TransitionSpec]` | `[]` | Edge/corner transition tiles between two terrains. |
| `animated_tiles` | `dict[str, AnimatedTileSpec]` | `{}` | Named tiles that cycle through other, already-declared tile ids. |
| `sample_map` | `SampleMap \| None` | `None` | An optional demo grid for visual sanity-checking / Godot's generated `TileMapLayer`. |

**`TileSpec`** — built from the same `Region`/shape DSL sprites use.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `size` | `Vec2` | required | This tile's pixel width/height. |
| `regions` | `dict[str, Region]` | `{}` | Drawable layers, local to this tile (a tile has its own `regions`, not the doc's). |
| `anchors` | `dict[str, Vec2]` | `{}` | Local anchors for this tile's regions. |
| `terrain` | `str \| None` | `None` | Which named terrain this tile visually belongs to. |
| `collision` | `str \| None` | `None` | Collision layer/shape tag; opaque string, not validated against a fixed set. |
| `navigation` | `bool` | `False` | Whether this tile contributes to the nav mesh. |
| `occlusion` | `bool` | `False` | Whether this tile occludes light (2D occluders). |

**`TerrainSet`**

| Field | Type | Default | Meaning |
|---|---|---|---|
| `mode` | `Literal["corners", "edges", "corners_and_edges"]` | `"corners_and_edges"` | Godot 4 terrain matching mode. |
| `tiles` | `list[str]` | `[]` | Member tile ids. |

**`TransitionSpec`**

| Field | Type | Default | Meaning |
|---|---|---|---|
| `from_terrain` | `str` | required | Source terrain name. |
| `to_terrain` | `str` | required | Terrain encroaching along the given edge/corner. |
| `tile_id` | `str` | required | Which declared tile renders this transition; must exist in `tiles` (`TIL001`). |
| `mask` | `str` | required | Edge/corner code: one of `N`, `NE`, `E`, `SE`, `S`, `SW`, `W`, `NW` (see `exporters/godot/tileset.py`'s `PEERING_BIT_NAMES`). |

**`AnimatedTileSpec`**

| Field | Type | Default | Meaning |
|---|---|---|---|
| `frames` | `list[str]` | required | Ordered tile ids to cycle through; must be non-empty and every id must exist in `tiles`. |
| `frame_duration_ms` | `int` | `200` | Per-frame duration; must be `> 0`. |
| `loop` | `bool` | `True` | Whether the cycle repeats. |

**`SampleMap`**

| Field | Type | Default | Meaning |
|---|---|---|---|
| `size` | `Vec2` | required | `(width, height)` in tiles. |
| `layers` | `dict[str, list[list[str]]]` | `{}` | `layer name -> rows of tile ids`; every row's length must equal `size`'s width and the row count must equal `size`'s height (`TIL005`). |

## Shared primitives (`schemas/common.py`)

**`Region`** — a named, layered group of shapes anchored at a world-space point. Shape
coordinates are relative to the region's own anchor, not absolute canvas coordinates.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `anchor` | `str` | required | Name of an entry in the containing doc's (or tile's) `anchors` map. |
| `layer` | `int` | required | Draw order; ascending, ties broken by region name (`plan_layers`). |
| `shapes` | `list[Shape]` | `[]` | The shape DSL ops that draw this region. |
| `mirror_safe` | `bool` | `True` | Whether this region participates in the flip-the-canvas mirroring strategy for a mirrored direction. `False` regions are composited unmirrored, on top, using the destination direction's own transforms. |
| `protected` | `bool` | `False` | Revision operations that would modify this region's shapes (`resize_region`, `translate_region`, `recolor_region`, `set_region_visibility`) raise `OperationError` instead. |

**Shape DSL** — `Shape = PixelShape | LineShape | RectShape | EllipseShape`, discriminated
on `op`. Every shape has `color` (a palette color id, not a literal RGBA) and `op`.

| Shape | `op` | Extra fields | Meaning |
|---|---|---|---|
| `PixelShape` | `"pixel"` | `at: Vec2` | One pixel. |
| `LineShape` | `"line"` | `start: Vec2`, `end: Vec2` | Integer Bresenham line, endpoints inclusive. |
| `RectShape` | `"rect"` | `at: Vec2`, `size: Vec2`, `fill: bool = True` | Axis-aligned rectangle; `fill=False` draws only the 1px border. |
| `EllipseShape` | `"ellipse"` | `at: Vec2`, `size: Vec2`, `fill: bool = True` | Ellipse inscribed in the `at`/`size` box; `fill=False` draws only the boundary ring. |

**`RegionTransform`** — a per-frame or per-direction-override delta applied to a region.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `offset` | `Vec2` | `(0, 0)` | Added to the region's anchor world position. Layers add component-wise (`merge_transforms`). |
| `visible` | `bool \| None` | `None` | `None` means "inherit from a lower-precedence layer"; the highest layer that sets it wins. `False` skips the region entirely for that frame. |
| `color_swap` | `dict[str, str]` | `{}` | Palette-id-to-palette-id remap applied to this region's shapes for this frame; higher layers win per key. |
| `scale_size` | `Vec2` | `(0, 0)` | Additive px growth on `rect`/`ellipse` shape sizes, applied symmetrically about centre (floor-division convention — see `rendering/compositor.py::_apply_scale_size`). No effect on `pixel`/`line` shapes. |

## Palette (`schemas/palette.py`)

**`PaletteColor`**

| Field | Type | Default | Meaning |
|---|---|---|---|
| `id` | `str` | required | Referenced by `Shape.color` and `RegionTransform.color_swap`. |
| `hex` | `str` | required | `#RRGGBB` or `#RRGGBBAA`, validated by regex. |
| `role` | `str \| None` | `None` | Free-text tag; `PIX010` looks for the substring `"shadow"`/`"light"` in this field. |
| `ramp` | `str \| None` | `None` | Free-text tag; any non-`None` value counts as "lighting metadata present" for `PIX010`. |

**`Palette`** (aliased as `PaletteRef`)

| Field | Type | Default | Meaning |
|---|---|---|---|
| `id` | `str` | required | Palette name. |
| `colors` | `list[PaletteColor]` | `[]` | Must have unique `id`s (validator). |

## Animation (`schemas/animation.py`)

**`AnimationSpec`**

| Field | Type | Default | Meaning |
|---|---|---|---|
| `loop` | `bool` | `True` | Whether the animation repeats. Read by `ANI003` (severity) and `ANI005` (only fires for looping animations). |
| `frames` | `list[FrameSpec]` | required | Must be non-empty. |
| `procedural` | `ProceduralAnimationSpec \| None` | `None` | Shader-driven animation contract (schema/exporter only — the local renderer does not execute shaders). |

**`FrameSpec`**

| Field | Type | Default | Meaning |
|---|---|---|---|
| `duration_ms` | `int` | required | Must be `> 0`. |
| `events` | `list[str]` | `[]` | Free-text event names fired on this frame (e.g. `"contact"`, `"hitbox_on"`). |
| `transforms` | `dict[str, RegionTransform]` | `{}` | Region name -> transform for this specific frame, merged over any `direction_overrides` layer. |

**`ProceduralAnimationSpec`**

| Field | Type | Default | Meaning |
|---|---|---|---|
| `shader` | `str` | required | Shader resource name, exporter-side only. |
| `params` | `dict[str, float \| int \| str \| bool]` | `{}` | Shader uniform values. |
| `target_region` | `str \| None` | `None` | Which region's node receives the material on export. |

## Project config (`schemas/project.py`)

**`ProjectConfig`** — `pixel-forge.yaml` at a project root.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `schema_version` | `Literal[1]` | `1` | |
| `name` | `str` | required | Project name. |
| `godot_baseline` | `str` | `"4.4"` | Documentary only; not enforced against the installed Godot version. |
| `assets_dir` | `str` | `"assets"` | Relative to project root. |
| `build_dir` | `str` | `"build"` | Relative to project root. |
| `references_dir` | `str` | `"references"` | Relative to project root. |
| `default_palette` | `str \| None` | `None` | Not currently read anywhere in `api.py` (not verified to have any effect). |

## Style profile (`schemas/style.py`)

**`StyleProfile`** — `references/style_profile.yaml`. Every descriptive field is a free
`str` (default `""`); a vision-capable agent fills them in after looking at reference
art (see `docs/references.md`).

| Field | Type | Default |
|---|---|---|
| `schema_version` | `Literal[1]` | `1` |
| `perspective`, `pixel_density`, `palette_tendencies`, `outline_style`, `light_direction`, `material_treatment`, `silhouette_complexity`, `texture_density`, `animation_timing`, `shape_language`, `environmental_hierarchy` | `str` | `""` each |
| `provenance` | `list[ProvenanceEntry]` | `[]` |

**`ProvenanceEntry`**

| Field | Type | Default | Meaning |
|---|---|---|---|
| `source_path` | `str` | required | Path to the reference file that informed a judgement. |
| `role` | `Literal["approved", "inspiration", "palette", "animation", "rejected"]` | required | Matches the `references/<role>/` subdirectory. |
| `notes` | `str` | `""` | Free text. |

## Validation results (`schemas/validation.py`)

**`Finding`**

| Field | Type | Default | Meaning |
|---|---|---|---|
| `rule_id` | `str` | required | E.g. `"PIX001"`. |
| `severity` | `Literal["error", "warning", "info"]` | required | |
| `kind` | `Literal["deterministic", "heuristic"]` | required | |
| `asset_id` | `str` | required | |
| `direction`, `animation`, `frame`, `region` | `str \| str \| int \| str`, all `\| None` | `None` | Whichever axes are relevant to this finding. |
| `message` | `str` | required | Human-readable description. |
| `remediation` | `str` | required | What to do about it. |
| `measurements` | `dict[str, float \| int \| str]` | `{}` | Structured numbers backing the message (pixel counts, ratios, hex colours, ...). |

**`ValidationReport`**

| Field | Type | Default | Meaning |
|---|---|---|---|
| `asset_id` | `str` | required | |
| `findings` | `list[Finding]` | `[]` | Sorted by `(rule_id, direction, animation, frame, region)`. |
| `blocking` | `bool` (computed) | — | `True` if any finding has `severity == "error"`. |
| `error_count` / `warning_count` | `int` (computed) | — | |

`ValidationReport.to_text()` renders the human-readable form the CLI prints.

## Revisions (`schemas/revision.py`)

**`OperationSpec`**

| Field | Type | Default | Meaning |
|---|---|---|---|
| `name` | `str` | required | One of the registered operation names (see `docs/revisions.md`). |
| `params` | `dict[str, JSONValue]` | `{}` | Operation-specific arguments. |
| `targets` | `dict[str, list[str]]` | `{}` | Reserved passthrough field; carried into the recorded inverse unchanged, not otherwise read by any handler (not verified to have any current effect). |
| `protect` | `list[str]` | `[]` | Anchor/region names that must be provably unchanged after the operation, or it raises `OperationError`. |

`JSONValue = float | int | str | bool | None | list[JSONValue] | dict[str, JSONValue]`.

**`RevisionRecord`**

| Field | Type | Default | Meaning |
|---|---|---|---|
| `revision_id` | `str` | required | Deterministic 12-char hash of `(parent, operation, hash_after)`. |
| `parent_revision` | `str \| None` | `None` | Previous revision's id, or `None` for the first revision. |
| `timestamp` | `str` | required | ISO-8601, supplied by the caller — never generated inside `api.py`. |
| `operation` | `OperationSpec` | required | What was applied. |
| `inverse` | `OperationSpec \| None` | `None` | The operation that undoes it exactly. |
| `asset_id` | `str` | required | |
| `affected_regions` / `affected_frames` / `affected_directions` | `list[str]` / `list[int]` / `list[str]` | `[]` each | Best-effort report of what the operation touched (see `revisions/operations.py::affected_targets`). |
| `hash_before` / `hash_after` | `str` | required | Full `content_hash` of the doc before/after. |
| `validation` | `ValidationReport \| None` | `None` | The validation result computed against the post-operation doc. |

**`RevisionDiff`** — same shape as the affected-target fields above, plus `operations`
(the ordered list of `OperationSpec` between two revisions), `hash_a`, `hash_b`.

## Build manifests (`schemas/manifest.py`)

**`AssetManifest`** — `build/<asset_id>/manifest.json`.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `asset_id` | `str` | required | |
| `asset_type` | `AssetType` | required | |
| `spec_hash` | `str` | required | The cache key `render_asset`/`build_asset` compare against. |
| `output_paths` | `dict[str, str]` | `{}` | Logical name (`sheet`, `contact_sheet`, `atlas`, `godot`) -> project-relative path. |
| `sheet` | `SheetManifest \| None` | `None` | Sprite sheet / atlas layout. |
| `preview_paths` | `dict[str, str]` | `{}` | `"{animation}_{direction}" -> path`. |
| `validation_summary` | `ValidationSummary` | required | Counts only — the full findings list is not persisted here (see `docs/mcp.md`'s note on `get_validation_report`). |

**`SheetManifest`** / **`SheetCellManifest`**: `image_path`, `columns`, `rows`,
`cell_size: Vec2`, and a `cells` list of `{direction, animation, index, x, y, w, h}`
pixel rects.

**`ValidationSummary`**: `blocking`, `error_count`, `warning_count`, `finding_count`.

### Godot import manifest — `GodotManifest` (`build/godot/<asset_id>.forge.json`)

| Field | Type | Default | Meaning |
|---|---|---|---|
| `manifest_version` | `Literal[1]` | `1` | The Godot plugin rejects any other value. |
| `asset_id` | `str` | required | |
| `asset_type` | `AssetType` | required | |
| `spec_hash` | `str` | `""` | Lets the plugin tell a no-op reimport from a real change. |
| `textures` | `dict[str, str]` | `{}` | Logical name -> path, relative to the manifest's own directory. |
| `sprite_frames` | `dict[str, SpriteFramesAnimation]` | `{}` | Sprite/enemy only; keyed `"{animation}_{direction}"`. |
| `pivots` | `dict[str, Vec2]` | `{}` | Per-direction pivot (the resolved `feet` anchor, or canvas bottom-centre if absent). |
| `baseline_y` | `int \| None` | `None` | Passed through from `asset.baseline_y`. |
| `events` | `dict[str, list[list[str]]]` | `{}` | `animation -> per-frame event lists`. |
| `tileset` | `GodotTileSetExport \| None` | `None` | Terrain only. |
| `animation_player` | `AnimationPlayerExport \| None` | `None` | Sprite/enemy/prop only. |
| `procedural` | `dict[str, ProceduralAnimationSpec]` | `{}` | Animations that carry a `procedural` block. |
| `import_settings` | `GodotImportSettings` | `filter="nearest", mipmaps=False, compress_mode="lossless", fix_alpha_border=False` | |

`build_godot_manifest` enforces exactly one of `{sheet, frames}` (sprite) or
`{atlas_cells}` (terrain) — passing the wrong combination raises `ExportError` rather
than emitting an empty section.

**`SpriteFramesAnimation`**: `loop: bool`, `frames: list[SpriteFrameEntry]`
(`{rect: AtlasRect, duration_ms: int}`). Godot fps/`duration_frames` derivation
(`derive_fps`, `duration_frames_for` in `exporters/godot/spriteframes.py`) is not
carried in the schema — the GDScript plugin recomputes it from `duration_ms`.

**`GodotTileSetExport`**: `atlas_source: str`, `tile_size: Vec2`,
`tiles: list[GodotTileCoord]`, `terrain_sets: dict[str, GodotTerrainSetExport]`,
`transitions: list[TransitionSpec]`, `terrain_bits: dict[str, dict[str, str]]`
(tile id -> peering-bit name -> terrain name, pre-resolved from `transitions`),
`animated_tiles: dict[str, GodotAnimatedTileExport]`, `sample_map`, and three tile-id
lists: `collision_tiles`, `navigation_tiles`, `occlusion_tiles`.

**`AnimationPlayerExport`**: `tracks: list[AnimationPlayerTrack]`
(`{node_path, property, keyframes: list[AnimationKeyframe]}`, `keyframes` carrying
`{time_ms: int, value: float | int | str | bool | Vec2}`). One track per
`(animation, region)` whose transform actually changes across frames.

## Programmatic access

`export_json_schemas(out_dir)` (`pixel-forge schemas export OUT_DIR`) writes
`<name>.schema.json` for `character`, `enemy`, `prop`, `terrain`, `validation_report`,
`godot_manifest`, `style_profile`, `revision_record`, and `project_config` —
deterministic JSON Schema, `sort_keys=True` plus a trailing newline, byte-identical
across runs.

## `source` (external frame files)

Optional on `character`, `enemy` and `prop`. When present, the asset's pixels come from
PNGs on disk (`rendering.external.ExternalFrameBackend`) instead of from `regions`,
which is then expected to be empty. Everything else on the document — `directions`,
`mirror`, `animations`, `anchors`, `palette`, `export`, `validation` — means exactly
what it means for a drawn asset, so validation, sheet packing, previews, per-direction
pivots and the Godot manifest all work unchanged.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `frames_dir` | `str` | `"frames"` | Directory holding the frames, resolved under the **asset's own directory** via `safe_join`. Cannot escape it. |
| `pattern` | `str` | `"{animation}_{direction}_{index}.png"` | Filename for one frame. Must reference all three placeholders and must be a bare filename, not a path. |
| `pins` | `dict[str, str]` | `{}` | `"{animation}_{direction}_{index}" -> sha256` of the file. Written by `pixel-forge source pin`. |

A frame whose direction appears in `mirror` reads its **source** direction's file and
flips it, exactly as `LocalRenderBackend` does — it never looks for a file of its own,
and it is never pinned. A direction with real artwork of its own should simply not be
listed in `mirror`.

Pins are optional so art can be iterated on before it is locked. Once a frame is
pinned, its file's sha256 is checked against the recorded pin on every `render`/
`build` call, including one `spec_hash` alone would let skip as already cached — so a
file that changed underneath its pin is a `RenderError`, with or without `--force`.
An **unpinned** `source:` asset has nothing to check against: it does not satisfy the
`RenderBackend` determinism contract, and `render`/`build` never treat it as cached —
every call re-renders from whatever is on disk at that moment.

```yaml
source:
  frames_dir: frames
  pattern: "{animation}_{direction}_{index}.png"
  pins:
    walk_e_0: 4f3a...
```
