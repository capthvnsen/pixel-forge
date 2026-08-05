# ADR 0001: Core architecture

## Status

Accepted. Reflects the shipped MVP (`src/pixel_forge/`).

## Context

Pixel Forge is a headless toolkit for producing pixel-art game assets, driven
primarily by AI coding agents rather than a human clicking around an image editor. It
needs to:

- Let an agent describe an asset (a character, an enemy, a prop, a terrain tileset)
  in a way that's easy to generate, diff, and edit programmatically.
- Turn that description into pixel-perfect PNGs deterministically, so the same spec
  always produces the same bytes — a hard requirement for caching, testing, and
  trusting an agent's edits without re-eyeballing every render.
- Catch mistakes automatically (off-palette colours, drifting anchors, seams that
  don't tile) instead of relying on a human noticing them in a review pass.
- Get the result into Godot 4 without hand-authored `.tres`/`.tscn` files, which are
  brittle to generate outside the editor and easy to corrupt.
- Be usable equally from a terminal (a human, or an agent via `claude -p`/similar)
  and from an MCP client (an agent operating it as a tool), without duplicating logic
  between the two.
- Leave room for the render step itself to later be replaced or augmented by a
  generative/vision model, without redesigning everything above it.

## Decisions

### 1. Structured YAML spec as the single source of truth

Every asset is one YAML file (pydantic-validated, `schema_version: 1`), never a raw
PNG a human hand-edits. **Every PNG the toolkit produces is a build artifact** —
deleting `build/` and re-running `render`/`build` reproduces it byte-for-byte. This
is the load-bearing design principle for the whole codebase (restated in `AGENTS.md`):
an agent (or a human) edits the spec, never the pixels.

### 2. A pure-domain core with no framework dependencies

`schemas` (pydantic models), `domain` (paths, project lifecycle, palette, geometry,
hashing, YAML I/O), `animation` (spec -> resolved frame expansion),
`rendering` (shape DSL -> `Canvas`), `validation` (rule engine + rules),
`preview` (GIF/WebP writers), `revisions` (operation registry + append-only log),
`references` (style profile), and `exporters.godot` (neutral JSON manifest) know
nothing about Typer, MCP, or each other's callers. None of them import `cli`, `mcp`,
or one another's presentation concerns. This is what makes the same logic reachable
identically from a terminal and from an MCP tool call — see decision 3.

### 3. One service layer (`api.py`) behind both the CLI and MCP server

`api.py` is the only module either `cli/commands.py` or `mcp/server.py` calls into.
Every `api.py` function returns a pydantic model, never prints, never calls
`sys.exit`, and never reads the clock (callers pass `timestamp` in explicitly — the
CLI's `_default_timestamp()` and an MCP client's own clock are the only places in the
codebase allowed to). `cli/commands.py` and `mcp/server.py` are both thin: each
function there parses its arguments/validates its input, calls exactly one `api.py`
function, and renders or returns the result. This means a bug fix or a new capability
in `api.py` is immediately available from both surfaces with no duplicated logic, and
a new interface (a future HTTP API, say) would be another thin wrapper over the same
`api.py`, not a third implementation of the domain logic.

### 4. Neutral JSON manifests plus a GDScript plugin, not hand-written `.tres`/`.tscn`

`exporters.godot` writes `build/godot/<asset_id>.forge.json` — a schema-versioned,
engine-agnostic description of textures, sprite frames, pivots, tileset/terrain data,
and import settings. A separate Godot 4 editor plugin (`godot/addons/pixel_asset_forge/`,
documented in `docs/godot.md`) reads that JSON and constructs native `SpriteFrames`,
`Animation`, and `TileSet` resources *through Godot's own APIs*. Nothing in this
Python codebase writes Godot's binary or text resource formats directly.

### 5. A small shape DSL, not raster editing primitives

An asset's visual content is expressed as named `regions`, each a `layer` of `shapes`
(`pixel`, `line`, `rect`, `ellipse`) referencing palette colour ids, anchored to a
named world-space point. This is deliberately closer to "vector description of a
sprite" than "paint program commands" — an agent can reason about and edit a rect's
`at`/`size` far more reliably than it could edit raw pixel data, and the same shape
list composes cleanly with `RegionTransform` deltas (offset, visibility, colour swap,
size) to drive per-frame animation without re-describing the shape every frame.

### 6. Determinism as a hard constraint, enforced at the seam

`rendering.backend.RenderBackend`/`TileRenderBackend` are `Protocol`s: for a given
`(doc, frame, palette)` (or `(doc, tile_id, palette)`), `render_frame`/`render_tile`
must return a byte-identical `Canvas` every time. The MVP's only implementation,
`LocalRenderBackend`, satisfies this trivially (no randomness anywhere in the shape
DSL -> pixel path). The contract is written into the Protocol's own docstring, not
just tested, because it's the property every cache (`render_asset`/`build_asset`'s
spec-hash skip logic), every golden-fixture test, and every "did this revision
actually change the pixels" workflow depends on. A backend that samples a generative
model would have to cache/pin its output to satisfy this, not skip it.

### 7. A discriminated `kind`/`asset.type` union for asset kinds

`AssetDoc = CharacterAsset | EnemyAsset | PropAsset | TerrainAsset`, discriminated on
a top-level `kind` field that mirrors `asset.type`. Pydantic's discriminated-union
machinery can only switch on a field of the model being validated, not on a field
nested inside `asset`, so `kind` exists purely to give the union something to
discriminate on — `parse_asset_doc` injects it from `asset.type` automatically, so
hand-authored YAML never specifies `kind` itself (specifying a mismatched one is
rejected by a model validator). Adding a fifth asset kind means adding one more
member to this union, not restructuring how any existing kind is parsed.

## Alternatives rejected

- **Freeform/untyped YAML (dict-of-dicts, validated ad hoc).** Rejected: loses
  pydantic's discriminated-union dispatch, loses `extra="forbid"` catching typos at
  parse time, and pushes validation logic into every consumer instead of one schema
  layer. An agent authoring a spec benefits enormously from immediate, precise
  validation errors (`SchemaError` reports the exact field path) instead of a
  downstream `KeyError` three modules later.
- **Direct pixel/raster editing as the spec format (a grid of colour indices).**
  Rejected as the *primary* authoring surface: it's what a human paints, not what an
  agent reliably edits — "widen this region by 2px" is a one-line operation against
  shape geometry and an ill-defined one against a raw pixel grid. The shape DSL is
  still ultimately rasterised (that's what rendering does), but editing happens one
  level up.
- **A single, monolithic CLI-only tool with MCP bolted on later (or vice versa).**
  Rejected in favour of the service-layer split from the start (decision 3): retrofitting
  a shared core after two independent implementations of "render an asset" diverge is
  far more expensive than designing the seam up front, and the plan explicitly needed
  both surfaces from day one.
- **Writing `.tres`/`.tscn` directly from Python.** Rejected: Godot's resource text
  format is an internal serialisation of engine-side C++ types, versioned per-engine
  and not documented as a stable external target. Emitting a neutral JSON manifest
  and letting a GDScript plugin build resources through Godot's own `SpriteFrames`/
  `TileSet`/`Animation` APIs means the plugin, not this codebase, tracks any format
  drift across Godot versions.
- **Letting the render backend introduce nondeterminism "when it doesn't matter"**
  (e.g. allowing antialiasing or floating-point coordinates in the local backend).
  Rejected: once any part of the pipeline is allowed to be approximately
  reproducible, the cache-by-content-hash mechanism, the golden-fixture tests, and
  "did this revision change the pixels" all become unreliable. Determinism was kept
  as an absolute, not a soft target.
- **A generic key-value "extra data" bag on every model instead of `extra="forbid"`.**
  Rejected: `extra="forbid"` turns a typo'd field name into an immediate,
  specific validation error instead of a silently-ignored no-op — valuable
  precisely because specs are meant to be authored by an agent that can't always
  proofread its own output the way a human would.

## Consequences

**Benefits:**

- The CLI and MCP server can never drift apart in behaviour — they are, by
  construction, two renderers of the same `api.py` outputs.
- Caching, revision hashing, and cross-run reproducibility all fall out of the
  determinism contract for free, without special-casing.
- New asset kinds, new render backends, and new validation rules each have one clear
  seam to extend (the discriminated union, the `RenderBackend` Protocol, and the
  `@register` decorator, respectively — see `AGENTS.md`), not a scattered set of
  places to touch.
- Godot version drift is contained to the GDScript plugin, which can be
  updated/re-verified independently of the Python codebase.

**Costs:**

- The shape DSL is a real expressiveness ceiling: it cannot express hand-painted,
  organic pixel art the way a human artist works directly on a canvas. It suits
  geometric, blocky, layered sprite styles well and suits painterly styles poorly —
  see `README.md`'s limitations.
- Two schemas exist for "an asset" and "what Godot needs" (`AssetDocUnion` vs.
  `GodotManifest`) rather than one — the exporter is real translation work, not a
  pass-through, and every new spec field that should reach Godot needs an explicit
  update to `exporters.godot` (and the GDScript plugin) to carry it across. This is
  documented as the "add a Godot exporter field" workflow in `AGENTS.md` precisely
  because it's a three-place change (schema, exporter, plugin) rather than automatic.
- The determinism constraint means a future generative/vision-model render backend
  must solve caching/pinning its own model outputs itself — the Protocol doesn't do
  that work for it, it only requires the result to be stable once obtained.
- One `regions`/`anchors` map per document, shared across every non-mirrored
  direction, means there's no way to give two directions genuinely different body
  silhouettes short of `direction_overrides` deltas (offset/visible/color_swap/
  scale_size) — a real limitation for asset types that need distinct per-direction
  art, not just equipment visibility toggles. See `examples/assets/engineer/README.md`
  for where this was hit directly.
