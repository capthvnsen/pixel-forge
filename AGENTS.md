# AGENTS.md

Instructions for coding agents working in this repository. Read this before editing
anything under `src/pixel_forge/`.

## Product purpose and the core design principle

Pixel Forge is a headless, AI-native pixel-art asset production toolkit for Godot 4.
It is a structured production system, not an image generator and not a drawing app.

**The editable source is the YAML spec. Every PNG the toolkit produces is a build
artifact.** Deleting `build/` and re-running `render`/`build` reproduces every image
byte-for-byte from the spec (see `RenderBackend`'s determinism contract, below).

Concretely, that means:

- **Never edit a generated image.** `build/<asset_id>/*.png`, `build/godot/*.forge.json`,
  and every Godot `.tres`/`.tscn` under `res://generated/` are computed from a spec.
  If a rendered result is wrong, fix the spec (directly, or via a revision operation)
  and re-render — don't touch the PNG.
- **Never hand-write a Godot resource.** `.tres`/`.tscn` files are constructed by the
  Godot plugin (`godot/addons/pixel_asset_forge/`) from the neutral
  `*.forge.json` manifest this codebase emits. If a resource needs to change, change
  the spec and/or the exporter that produces the manifest, not the resource.
- **An asset's identity is its spec file**, `assets/<id>/<id>.yaml` — everything else
  (`build/`, `references/style_profile.yaml`, `revisions.jsonl`) is either derived
  from it or metadata about edits to it.

## Architectural boundaries

Dependency direction, as a rule an agent can check before adding an import:

**Domain logic never imports `cli`, `mcp`, or `exporters`.** `schemas`, `domain`,
`animation`, `rendering`, `validation`, `preview`, `revisions`, and `references` must
never import from `pixel_forge.cli`, `pixel_forge.mcp`, or `pixel_forge.exporters`.
`exporters.godot` may depend on the domain packages, but nothing in the domain
depends back on it.

**The CLI and MCP contain no logic and call only `api.py`.** Every function in
`cli/commands.py` and every `@mcp_server.tool()` in `mcp/server.py` parses/validates
its own input, calls exactly one `pixel_forge.api` function, and renders or returns
the result. If you find yourself writing an `if`/`for` that isn't argument parsing or
output formatting inside either module, that logic belongs in `api.py` (or deeper)
instead.

**`api.py` never prints, never calls `sys.exit`, never reads the clock.** Every
`api.py` function returns a pydantic model. Timestamps are supplied by the caller
(`cli/commands.py::_default_timestamp()` is the CLI's one clock-reading point); if you
add a function that needs "now", take it as a parameter, don't call `datetime.now()`
inside `api.py`.

Verify quickly: `grep -rn "^from pixel_forge\.\(cli\|mcp\|exporters\)" src/pixel_forge/{schemas,domain,animation,rendering,validation,preview,revisions,references}` should return nothing.

## Repository map

```
src/pixel_forge/
  schemas/       Pydantic models: the asset spec, palette, animation, revisions,
                  build manifests, style profile, validation report. The contract
                  every other package validates against or produces.
  domain/        Pure core: project lifecycle (Project, ProjectPaths), path safety
                  (safe_join, validate_asset_id), palette resolution, anchor/geometry
                  math, content hashing, YAML <-> pydantic I/O.
  animation/     Spec -> ResolvedFrame/ResolvedTileFrame expansion (directions x
                  animations x frames, with mirroring and transform-merging resolved).
  rendering/     Canvas (numpy RGBA raster), the shape DSL -> pixel drawing, the
                  RenderBackend/TileRenderBackend Protocol seam, LocalRenderBackend,
                  sprite-sheet/atlas packing, seam checking.
  preview/       Deterministic GIF/WebP animated-preview writers.
  validation/    Rule registry (@register), the runner, and PIX0xx/ANI0xx/TIL0xx rules.
  revisions/     Operation registry + handlers (resize_region, translate_region, ...),
                  the append-only revisions.jsonl log, revision diffing.
  references/    references/ directory scaffolding, the style-profile schema + I/O.
  exporters/
    godot/       AssetDocUnion -> neutral *.forge.json GodotManifest (manifest.py,
                  spriteframes.py, animation.py, tileset.py). JSON only, never .tres.
  api.py         The service layer: the only entry point the CLI and MCP call.
  cli/           Typer app (main.py: wiring; commands.py: one function per command).
  mcp/           MCP server (server.py: one @tool per pixel_forge.api function).
  templates.py   Minimal starter specs for `new`/`create_asset`, one per asset type.
  errors.py      The ForgeError exception hierarchy every deliberate failure uses.

godot/           Sample Godot 4.4 project + the pixel_asset_forge editor plugin
                  (see docs/godot.md — written and owned by another agent workstream).
examples/        Four worked example asset projects (engineer, crawler, beacon,
                  forest_tileset) under examples/assets/, plus examples/pixel-forge.yaml.
tools/           godot_headless_import.sh: CI-safe headless Godot import wrapper.
tests/
  unit/          Fast, isolated tests per module.
  integration/   Cross-module flows through api.py.
  end_to_end/    Full spec -> render -> validate -> export flows, incl. the four
                  examples and the Godot plugin's static checks.
  golden/        Byte-for-byte fixture comparisons (see "verify generated outputs").
docs/            This documentation set.
schemas/         (empty at the repo root; JSON Schema output goes wherever
                  `pixel-forge schemas export OUT_DIR` is pointed — not a fixed
                  location in the repo.)
```

## Setup, test, lint, typecheck

```bash
uv sync                       # install dependencies
uv run pytest                 # 422 tests as of this doc; must all pass
uv run mypy                   # strict mode (see pyproject.toml [tool.mypy]); must be clean
uv run ruff check .           # lint; must be clean
uv run ruff format .          # format
uv run pixel-forge --help     # smoke-test the CLI entry point
```

## Asset schema concepts

Full field-by-field reference: `docs/schema.md`. The concept chain:

**spec** (`schema_version`, `asset.{id,type,canvas,...}`) -> **palette** (named
colour ids, referenced by shapes, never literal RGBA in the spec) -> **anchors**
(named world-space points) -> **regions** (a named, layered group of shapes, each
anchored to one anchor point; shape coordinates are anchor-relative, not
canvas-absolute) -> **shape DSL** (`pixel`/`line`/`rect`/`ellipse`, each referencing
a palette colour id) -> **transforms** (`RegionTransform`: `offset`, `visible`,
`color_swap`, `scale_size` — a per-frame or per-direction-override delta applied to a
region, merged lowest-to-highest precedence) -> **directions/mirroring** (`directions`
list; `mirror: {dst: src}` flips a rendered source direction's canvas rather than
duplicating its geometry) -> **animations** (`AnimationSpec.frames`, each a
`FrameSpec` with `duration_ms`, `events`, and per-region `transforms`) ->
**export/validation options** (`ExportOptions`, `ValidationOptions` — sheet layout,
preview format, and the six knobs that relax specific validation rules).

Terrain assets (`TerrainAsset`) replace `regions`/`animations`/`anchors` at the
document level with `tiles: dict[str, TileSpec]` (each `TileSpec` has its *own*
local `regions`/`anchors`, built from the same shape DSL), plus `terrain_sets`,
`transitions`, `animated_tiles`, and an optional `sample_map`.

### Worked example (verified against `parse_asset_doc`)

A minimal but complete `prop` asset — an animated torch:

```yaml
schema_version: 1

asset:
  id: torch
  type: prop
  canvas: [16, 16]

palette:
  id: torch_palette
  colors:
    - { id: wood, hex: "#5b3a29" }
    - { id: flame, hex: "#f2a93b" }

directions: [south]
anchors:
  root: [8, 15]

regions:
  handle:
    anchor: root
    layer: 0
    shapes:
      - { op: rect, color: wood, at: [-1, -10], size: [2, 10] }
  flame:
    anchor: root
    layer: 10
    shapes:
      - { op: ellipse, color: flame, at: [-3, -14], size: [6, 6] }

animations:
  burn:
    loop: true
    frames:
      - { duration_ms: 150, transforms: { flame: { offset: [0, 0] } } }
      - { duration_ms: 150, transforms: { flame: { offset: [0, -1] } } }

export: {}
validation: {}
```

Verified directly against the parser:

```python
>>> from pixel_forge.schemas import parse_asset_doc
>>> import yaml
>>> doc = parse_asset_doc(yaml.safe_load(open("torch.yaml")))
>>> type(doc).__name__, doc.asset.id, doc.kind
('PropAsset', 'torch', 'prop')
```

(Run and confirmed against this exact document while writing this doc — see the
`AGENTS.md`-authoring session; no `torch.yaml` ships in the repo, this is the spec
verbatim.)

For a much larger worked spec exercising direction mirroring, `direction_overrides`,
multi-layer regions, and a non-looping event-bearing animation, read
`examples/assets/engineer/engineer.yaml`.

## How to add an asset type

`AssetType = Literal["character", "enemy", "prop", "terrain"]`
(`schemas/asset.py`). `kind` exists on every `BaseAssetDoc` subclass purely because
pydantic's discriminated-union machinery can only discriminate on a field of the
model being validated, not on a field nested inside `asset` — `kind` mirrors
`asset.type` at the top level so `AssetDoc = Annotated[AssetDocUnion,
Field(discriminator="kind")]` has something to switch on. `parse_asset_doc` injects
`kind` from `asset.type` automatically, so hand-authored YAML never specifies it (a
model validator rejects a document where the two disagree, catching a
programmatically-constructed mismatch).

To add a fifth kind:

1. Add the literal to `AssetType`.
2. Define the new pydantic model (extend `SpriteAssetBase` for a directional/animated
   sprite type, or `BaseAssetDoc` directly for something structurally different like
   `TerrainAsset`), with `kind: Literal["your_type"] = "your_type"`.
3. Add it to `AssetDocUnion` in `schemas/asset.py`.
4. Add a starter template in `templates.py::asset_template` — must render and validate
   with zero blocking findings (see the test asserting this for the existing four).
5. Extend `validation`'s `applies_to` tuples for any rule that should also run against
   it (or add new rules scoped to it, see below).
6. Extend `exporters.godot.manifest.build_godot_manifest` to handle the new kind (or
   explicitly decide it isn't exportable yet and say so).
7. Add it to `schemas/export_schemas.py::_TARGETS` so `pixel-forge schemas export`
   picks it up.

## How to add a validator

Rules live in `validation/rules_pixel.py`, `rules_animation.py`, or
`rules_tileset.py`, registered with `@register(rule_id, severity=..., kind=...,
applies_to=..., description=...)` (`validation/engine.py`). Conventions:

- **Rule id**: `PIX0xx` (per-frame raster checks), `ANI0xx` (cross-frame/animation
  checks), `TIL0xx` (terrain-only checks) — pick the next unused number in the
  relevant family. `ENG001` is reserved for the engine's own "a rule crashed" finding.
- **`kind: "deterministic"` vs `"heuristic"`**: deterministic means the same doc
  always produces the same findings for that rule, full stop. Heuristic means it's a
  judgement call (a threshold, a "most likely" interpretation) that can have false
  positives/negatives on real art. Be honest about which one a new rule is — it's
  documented per-rule in `docs/validation.md` and callers rely on the distinction to
  decide how much to trust a finding.
- Build the `Finding` via the shared `make_finding(ctx, rule_id, severity, kind, ...)`
  helper, not `Finding(...)` directly — it stamps `asset_id` consistently.
- Every finding needs a `message` (what's wrong) and a `remediation` (what to do about
  it) — not just one or the other.
- **Two tests per rule**: one asserting the rule *fires* on a doc constructed to
  trigger it, one asserting it stays silent on a doc that shouldn't trigger it (see
  `tests/unit/` for the existing pattern per rule). A rule with only a "fires"
  test hasn't proven it isn't trivially over-firing.
- A rule function receives a `RuleContext` (`doc`, `palette`, already-rendered
  `frames`/`tiles`, `resolved` frame metadata) and returns `list[Finding]` — it never
  renders anything itself.

## How to add a render backend

`RenderBackend`/`TileRenderBackend` (`rendering/backend.py`) are `Protocol`s, not a
base class — anything with the right method signature satisfies them; no inheritance
required. The one deliberate seam for a future generative-image or vision-model
backend.

**Determinism contract**, stated in the Protocol's own docstring and non-negotiable:
for a given `(doc, frame, palette)` or `(doc, tile_id, palette)`,
`render_frame`/`render_tile` must return a byte-identical `Canvas` every time it is
called. No randomness, no timestamps, no network/model variance leaking into pixels.
A backend that samples an external model must cache/pin the result so repeat renders
against an unchanged spec agree exactly — this is what the whole cache-by-content-hash
mechanism (`render_asset`, `build_asset`) and every golden-fixture test depend on.

`api.py` and the validators call these Protocols (via `render_asset_frames`/
`render_terrain_tiles`, which default to `LocalRenderBackend` when no backend is
passed), never `LocalRenderBackend` directly — a new backend plugs in at that call
site, not by modifying `api.py`.

## How to add a Godot exporter field

A new spec field reaching the Godot plugin is a three-place change, always in this
order:

1. **Schema** (`schemas/asset.py` or wherever the source field lives): add the field
   to the spec model.
2. **Exporter** (`exporters/godot/manifest.py` and/or `spriteframes.py`/
   `animation.py`/`tileset.py`): read the new spec field and add a corresponding field
   to the relevant `schemas/manifest.py` model (`GodotManifest`/`GodotTileSetExport`/
   etc.) — extend the manifest schema itself if there's nowhere to put the value yet
   (see the integration notes atop `spriteframes.py` and `animation.py` for two fields
   that were deliberately *not* added this way, and why).
3. **Plugin** (`godot/addons/pixel_asset_forge/importer.gd`, and
   `manifest_validator.gd` if the field needs validation): read the new manifest
   field and apply it to the constructed Godot resource.

Then **regenerate the golden fixtures**: `tests/golden/fixtures/godot/*.forge.json`
must reflect the new field, and `tests/end_to_end/test_godot_plugin.py` checks that
every manifest field the GDScript reads actually exists on `GodotManifest` — so a
schema/exporter change without a matching plugin read (or vice versa) is caught
automatically. Regenerate with `UPDATE_GOLDEN=1` (see below), then read the diff by
hand before committing it — an automated regeneration is not itself a review.

## Safe path rules

Every filesystem path the CLI/MCP touches routes through `domain/paths.py`:

- **`safe_join(root, *parts)`** is the sole security boundary. It resolves `root` and
  the candidate path and requires the result to be `root` itself or a descendant —
  catching `..`, an absolute path component, a literal `~` (never expanded), or a
  symlink whose target escapes root (via `Path.resolve()`'s symlink-walking
  behaviour). Never build a path into a project with plain `Path.joinpath`/`/` —
  always `safe_join`.
- **`validate_asset_id(asset_id)`** enforces `^[a-z0-9][a-z0-9_]*$`, max 64 chars,
  before an id is ever used as a path component. Called at the top of every
  asset-scoped `api.py` function (`_load_doc`, `new_asset`, ...) before touching the
  filesystem — an invalid id raises `PathSecurityError` immediately, not somewhere
  deep inside a file open.
- **Every path a project needs goes through `ProjectPaths`** (`domain/paths.py`),
  never constructed ad hoc: `asset_spec(id)`, `asset_revisions(id)`,
  `build_asset_dir(id)`, `build_godot_dir()`, etc. — each one calls `safe_join`
  internally, so a new call site gets the security boundary for free instead of
  needing to remember to add it.

## Files agents must not manually generate or edit

- Anything under `build/` (any project's build output) — always derived from a spec;
  re-render instead of hand-editing.
- Any `.tres`/`.tscn` file, anywhere — always constructed by the Godot plugin from a
  `*.forge.json` manifest.
- `schemas/*.schema.json` (wherever `pixel-forge schemas export` writes them) —
  generated from the pydantic models; edit the model, then regenerate.
- Golden fixture PNGs under `tests/golden/fixtures/` — regenerate rather than hand-edit
  when a rendering change is intentional:

  ```bash
  UPDATE_GOLDEN=1 uv run pytest tests/golden/
  ```

  then inspect the resulting diff by hand before committing — a passing golden test
  after regeneration only proves the new output is internally consistent, not that
  it's the output you meant to produce.

## Using subagents on this repo

- Give each subagent a **bounded task** against a **disjoint set of files** — this
  repo's package boundaries (`schemas`, `domain`, `rendering`, `validation`,
  `revisions`, `exporters/godot`, `cli`, `mcp`, `docs/*`) are natural task boundaries
  precisely because the architecture keeps them decoupled (see "Architectural
  boundaries" above). Two subagents editing the same package concurrently will
  conflict; two editing different packages usually won't.
- State the **required return format** up front: for a code change, "tests pass,
  mypy strict clean, ruff clean, plus a one-paragraph summary of what changed and
  why"; for a research task, a direct answer with file:line references, not a
  narrated tour.
- Never let a subagent touch `examples/` or `docs/godot.md` unless that is
  specifically its assigned task — both are easy to silently clobber if a subagent's
  scope is stated loosely as "the docs" or "the sample project."
- A subagent should run `uv run pytest`, `uv run mypy`, and `uv run ruff check .`
  itself before reporting a code change done — don't take "I edited the file" as
  evidence of a working change.

## How to run the four MVP examples

```bash
uv run pixel-forge build-all --root examples
```

Builds `engineer` (character), `crawler` (enemy), `beacon` (prop), and
`forest_tileset` (terrain) in one pass — render, preview, export, per asset. See each
asset's own `examples/assets/<name>/README.md` for what it's specifically designed to
demonstrate, and `docs/validation.md` for why each asset's non-zero findings (all
`warning`/`info`, never blocking) are expected rather than bugs.

## How to verify generated outputs

- **Render twice, compare bytes** — the determinism contract in one command:

  ```bash
  pixel-forge render <id> --root <project> --force
  sha256sum build/<id>/<id>_sheet.png
  pixel-forge render <id> --root <project> --force
  sha256sum build/<id>/<id>_sheet.png   # must match exactly
  ```

- **Run validation** and confirm `blocking` is `false` (or, if it's `true`, that every
  blocking finding is one you intended to leave failing pending a fix):

  ```bash
  pixel-forge validate <id> --root <project>   # exits 1 if blocking
  ```

- **Check the manifest parses** — `build/<id>/manifest.json` should round-trip
  through `AssetManifest.model_validate_json`, and `build/godot/<id>.forge.json`
  through `GodotManifest.model_validate_json`; both are exercised automatically by
  `tests/golden/` and `tests/end_to_end/`, but worth a manual spot-check after a
  schema change.
- For Godot-side verification (does the manifest actually produce correct Godot
  resources), see `docs/godot.md`'s "Manual verification steps" — that layer needs a
  real Godot install and is not otherwise automated in CI.

## Definition of done for a code change in this repo

1. `uv run pytest` passes in full (422 tests as of this doc — check the current count
   didn't silently drop).
2. `uv run mypy` is clean under strict mode.
3. `uv run ruff check .` is clean.
4. Determinism is preserved: if the change touches rendering, the "render twice,
   compare bytes" check above still holds.
5. New behaviour has a test — a new validation rule has its two tests (fires /
   doesn't fire), a new revision operation has round-trip (`apply` then `apply` the
   inverse) coverage, a new exporter field has golden-fixture coverage.
6. If the change touches `exporters.godot`, the golden fixtures are regenerated
   (`UPDATE_GOLDEN=1`) and the diff has been read by hand, not just accepted.
