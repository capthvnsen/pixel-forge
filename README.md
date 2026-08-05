# Pixel Forge

A headless, AI-native pixel-art asset production toolkit for Godot 4. It is a
structured production system, not an image generator and not a drawing app: assets
are described as a structured YAML spec (palette, anchors, layered regions, a small
shape DSL, per-frame transforms), rendered deterministically into PNGs, checked by an
automated validation rule set, and exported into Godot 4 via a neutral JSON manifest
plus an editor plugin — never hand-authored `.tres`/`.tscn` files. See
`docs/adr/0001-architecture.md` for the full architecture rationale, and `AGENTS.md`
if you are a coding agent editing this repository.

## Requirements

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/)
- Godot 4.4+ for the editor plugin (not required to author, render, validate, or
  revise assets — only to import them into a Godot project)

## Install

```bash
uv sync
uv run pixel-forge --version
```

Every command below is invoked as `uv run pixel-forge ...` from the repository root
(the entry point is registered as `pixel-forge = "pixel_forge.cli.main:app"` in
`pyproject.toml`). Full command reference: `docs/cli.md`.

## Quick start

A complete workflow: create a project, create an asset, render it, validate it,
preview it, export it, revise it, rebuild. Every block below is the real output of
running these exact commands.

```console
$ uv run pixel-forge init ./demo --name demo
initialised project 'demo' at /path/to/demo

$ uv run pixel-forge new character hero --root ./demo
hero (character): 2 frame(s), spec_hash=2e44719c5f6f445dc5e2c514c30673b01dcf91ecbe7496b53f450718a5de7edb
  spec: assets/hero/hero.yaml
  directions: south
  animations: idle

$ uv run pixel-forge render hero --root ./demo
hero: rendered, 2 frame(s) written
  sheet: build/hero/hero_sheet.png
  contact_sheet: build/hero/hero_contact.png

$ uv run pixel-forge validate hero --root ./demo
hero: 0 error(s), 0 warning(s)
  INFO PIX010: doc carries no lighting metadata (no palette colour role/ramp declared)

$ uv run pixel-forge preview hero --root ./demo
hero: format=gif
  idle_south: build/hero/preview_idle_south.gif

$ uv run pixel-forge export godot hero --root ./demo
hero (character): spec_hash=2e44719c5f6f445dc5e2c514c30673b01dcf91ecbe7496b53f450718a5de7edb
  texture[sheet]: build/hero/hero_sheet.png
  animations: idle_south

$ uv run pixel-forge revise hero \
    --operation translate_region --param region=block --param 'offset=[1,0]' \
    --timestamp 2026-08-05T12:00:00Z --root ./demo
e771348e3959: translate_region on hero at 2026-08-05T12:00:00Z
  hash: 2e44719c5f6f445dc5e2c514c30673b01dcf91ecbe7496b53f450718a5de7edb -> dbab27abcd7b95cb6e2b43af59cc343252de27bf7c23bde7620b32533519f4ff
  regions: block

$ uv run pixel-forge build hero --root ./demo
hero (character): spec_hash=dbab27abcd7b95cb6e2b43af59cc343252de27bf7c23bde7620b32533519f4ff
  contact_sheet: build/hero/hero_contact.png
  sheet: build/hero/hero_sheet.png
  godot: build/godot/hero.forge.json
  validation: 0 error(s), 0 warning(s)
```

`build` is the single-command version of render + preview + export; `render` and
`build` are both cached against the spec's content hash, so re-running either without
`--force` after a no-op edit is a no-op (`skipped: true`). `PIX010`'s `INFO` finding
is expected for a starter template — it just means no palette colour has declared a
lighting `role`/`ramp` yet (see `docs/validation.md`).

The `--operation`/`--param` shape (and the full operation catalogue: `resize_region`,
`translate_region`, `recolor_region`, `set_frame_duration`, `add_frame`,
`remove_frame`, `set_region_visibility`) is documented in `docs/revisions.md`, along
with a worked, non-trivial example (widening a region on a real four-direction
character without touching its feet, weapon, or palette) and the exact revision
record it produces.

Add `--json` (before the subcommand, e.g. `pixel-forge --json inspect hero --root .`)
to any command for structured output instead of the text form above.

## The four shipped examples

`examples/` contains one worked spec per asset type: `engineer` (character, four
directions with mirroring), `crawler` (enemy, combat metadata), `beacon` (prop,
layered transform + procedural-shader animation), `forest_tileset` (terrain, full
8-mask transitions, animated water, a sample map). Each has its own
`examples/assets/<name>/README.md` explaining exactly what it demonstrates and why
its validation findings (all `warning`/`info`, never blocking) are expected.

```bash
uv run pixel-forge build-all --root examples
```

```
built 4 asset(s), 16 finding(s) total
  beacon: ok
  crawler: ok
  engineer: ok
  forest_tileset: ok
```

## Importing into Godot

Full instructions, including manual verification steps and known Godot-side
limitations: `docs/godot.md`. Short version: the sample project at `godot/` ships the
`pixel_asset_forge` plugin pre-enabled; point its dock at a directory of
`*.forge.json` manifests (produced by `export godot`/`build`) and click Import, or run
headlessly:

```bash
tools/godot_headless_import.sh [MANIFEST_DIR]
```

## Running the MCP server

Full tool reference and a worked agent workflow: `docs/mcp.md`.

```bash
uv run python -m pixel_forge.mcp.server /path/to/project
# or: PIXEL_FORGE_PROJECT=/path/to/project uv run python -m pixel_forge.mcp.server
```

Sample client config block (a generic stdio MCP client; exact keys vary by client):

```json
{
  "mcpServers": {
    "pixel-forge": {
      "command": "uv",
      "args": ["run", "python", "-m", "pixel_forge.mcp.server", "/path/to/project"]
    }
  }
}
```

The project root is fixed once at server startup — never a per-tool parameter — so a
calling agent cannot point any tool outside the project the server was launched
against.

## Architecture

```
schemas            pydantic models: the spec, palette, animation, revisions,
                    build/Godot manifests, validation report, style profile
   |
domain              paths/project lifecycle, palette resolution, geometry,
                     content hashing, YAML I/O  (pure, no framework deps)
   |
animation            spec -> resolved (direction x animation x frame) expansion
   |
rendering / validation / preview / revisions / exporters.godot
   |  shape DSL -> pixels (RenderBackend Protocol seam)
   |  rule engine: PIX0xx / ANI0xx / TIL0xx
   |  deterministic GIF/WebP writers
   |  operation registry + append-only revision log
   |  AssetDocUnion -> neutral *.forge.json manifest
   |
api.py               the one service layer: every function below is pydantic-in,
                      pydantic-out, no printing, no sys.exit, no clock reads
   |
  +-- cli/            Typer app: one function per command, calls exactly one
  |                    api.py function, renders the result
  +-- mcp/             MCP server: one @tool per api.py function, returns the
                        result unchanged
```

Every layer above `api.py` is a thin renderer of the same calls; see
`docs/adr/0001-architecture.md` for why this shape was chosen and what it costs.

## Known limitations

Read honestly against the code, not softened:

- **Heuristic validation rules are untuned against real art.** `PIX006`-`PIX010`,
  `ANI005`/`ANI006`/`ANI008`, and `TIL007` all use thresholds (e.g. `PIX007`'s 10%
  minority-edge-colour cutoff, `ANI005`'s 35% loop-pop threshold, `ANI008`'s 40%
  silhouette-volume-change threshold) chosen to pass the four shipped examples
  cleanly, not validated against a broader corpus of hand-drawn pixel art. Expect
  false positives on legitimately busy/organic art and false negatives on subtler
  mistakes. See `docs/validation.md` for the full rule table.
- **The only render backend is the local, deterministic shape-DSL renderer.**
  `RenderBackend`/`TileRenderBackend` (`rendering/backend.py`) are a `Protocol` seam
  deliberately left open for a future generative-image or vision-model backend, but
  no such backend ships — `LocalRenderBackend` is the only implementation in this
  repository.
- **Per-direction art is expressed as transform overrides, not independent
  per-direction artwork.** A sprite asset has exactly one `regions`/`anchors` map,
  shared by every non-mirrored direction; `direction_overrides` can toggle visibility,
  offset, colour, or size deltas per direction, but cannot give two directions a
  genuinely different silhouette the way independently hand-drawn per-direction art
  would. See `examples/assets/engineer/README.md`'s "Schema limitation hit" section
  for where this was hit directly.
- **Revision operations apply to sprite assets only, not terrain.** Every operation
  handler in `revisions/operations.py` raises `OperationError` against a `TerrainAsset`
  — there is currently no revision catalogue for tile edits; a terrain spec must be
  hand-edited or replaced wholesale (MCP `update_asset_spec`).
- **The toolkit performs no image analysis of reference art.** `references/profile.py`
  scaffolds directories and stores/merges structured style judgements, but nothing in
  this codebase reads pixels out of a reference image — a vision-capable agent (or a
  human) is expected to look at the references and write the style profile. See
  `docs/references.md`.
- **The manifest cannot express some Godot tile-animation arrangements.** Godot's
  `TileSetAtlasSource` animation model requires an animated tile's frames to occupy a
  contiguous horizontal atlas strip; the neutral manifest schema instead lists
  arbitrary already-named tile ids as frames (to let an animated tile reuse
  already-declared static tiles, e.g. a water shimmer cycling through existing tiles).
  When a tile's frames don't form such a strip, the Godot plugin leaves that tile
  static and emits a warning rather than misbehaving — see `docs/godot.md`'s "Known
  limitations" for this and several more Godot-side specifics (texture filtering,
  per-tile terrain assignment, prop `SpriteFrames`).
- **A few schema fields are accepted but currently unused.** `ExportOptions.godot`,
  `ProjectConfig.default_palette`, and `OperationSpec.targets` all exist in the
  schema and are not read as a gate or otherwise acted on anywhere in `api.py` (not
  verified to have any effect on behaviour) — they're forward-looking schema surface,
  not yet wired to anything.
- **`test-seams` output is unfiltered and grows quadratically.** For N terrain tiles
  it prints `4N²` lines (every tile pair, every edge); usable directly for small
  tilesets, meant to be consumed via `--json` or piped through `grep`/`sort` for
  larger ones.

## License

MIT — see `pyproject.toml`.
