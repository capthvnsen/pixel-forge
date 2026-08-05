# MCP server reference

`src/pixel_forge/mcp/server.py` exposes `pixel_forge.api` as MCP tools for AI agents.
Every tool validates its input, calls exactly one `pixel_forge.api` function, and
returns that function's pydantic result model unchanged. No rendering, validation, or
revision logic lives in this module.

**SDK note:** the installed package is `mcp==2.0.0`, which ships
`mcp.server.mcpserver.MCPServer` — there is no `mcp.server.fastmcp.FastMCP` in this
version (confirmed: `from mcp.server.fastmcp import FastMCP` raises `ModuleNotFoundError`
against the installed package). `MCPServer` is its direct successor and has the same
`@server.tool()`-decorator shape used throughout this file.

## Project root

The project root is fixed once at process startup, never a per-tool parameter — a
calling agent cannot point any tool outside the project the server was launched
against. Every asset id still flows through `api.py`'s own `validate_asset_id`/
`safe_join` checks, which reject path traversal (`"../evil"`, absolute paths, `~`) at
the domain layer independently of that fixed root.

```bash
python -m pixel_forge.mcp.server /path/to/project
# or
PIXEL_FORGE_PROJECT=/path/to/project python -m pixel_forge.mcp.server
```

Reads `argv[1]`, falling back to the `PIXEL_FORGE_PROJECT` env var; exits `2` with a
usage message if neither is set.

## Error handling

Every tool calls `_guard(fn)`, which translates a `ForgeError` into a structured
`MCPError(code=INVALID_PARAMS, message=str(exc))` — the message is passed through
verbatim, since `ForgeError` subclasses already name the problem and the fix (e.g.
`AssetNotFoundError` lists known ids, `ExportError` names the missing render step).
Any other exception is left to the SDK's own tool-execution wrapper, which returns a
non-protocol error without leaking a Python traceback.

## Sample client config

A generic stdio MCP client config block (exact key names vary by client):

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

## Tools

### Project lifecycle

**`initialize_asset_project(name: str, dry_run: bool = False) -> ProjectConfig`**
Create (or verify) the project at the server's fixed root: writes `pixel-forge.yaml`
and the `assets/`, `build/`, `references/` directories. Idempotent for the same
`name`; a different `name` on an existing project is an error. Call once before any
other tool if the root has no `pixel-forge.yaml` yet.

**`list_assets() -> list[AssetSummary]`**
Every asset's id, type, spec path, animations, directions, frame count, spec hash.
Call this to discover valid asset ids before any asset-scoped tool.

**`get_asset(asset_id: str) -> AssetDocUnion`**
The full parsed spec document: regions, anchors, animations, palette, export/
validation options, and (terrain) tiles. Inspect before editing with
`apply_asset_operation`/`update_asset_spec`.

**`create_asset(asset_type: AssetType, asset_id: str, dry_run: bool = False) -> AssetSummary`**
New asset from the minimal starter template. Fails if the id already exists. The
template is guaranteed to render/validate with zero blocking findings.

**`update_asset_spec(asset_id: str, spec: dict[str, JSONValue], timestamp: str) -> RevisionRecord`**
Replace the entire spec document in one shot and record it as a revision (operation
name `"replace_spec"`, a real entry in `revisions/operations.py`'s registry). `spec`
is the full document as it appears in YAML, minus `kind` (derived from `asset.type`).
Rejects a spec whose `asset.id` doesn't match `asset_id`, one that fails schema
validation, or one that touches a `protected: true` region. Being a real, registered
operation, a `replace_spec` revision is revertible like any other. Use this for
structural edits the operation DSL doesn't cover (adding a region, changing
directions, editing palette colours); use `apply_asset_operation` for the smaller
invertible edits it already knows. The CLI exposes the same behaviour as
`pixel-forge update-spec <asset_id> --file <path.yaml>`.

```
current = get_asset(asset_id="hero")
spec = current.model_dump(mode="json")
spec["directions"].append("north")
update_asset_spec(asset_id="hero", spec=spec, timestamp="2026-08-05T12:00:00Z")
```

### Rendering

**`render_asset(asset_id: str, force: bool = False, dry_run: bool = False) -> RenderResult`**
Render frames, sprite sheet, contact sheet under `build/<asset_id>/`. Idempotent: a
second call against an unchanged spec with `force=False` is a no-op
(`result.skipped is True`), cached against the spec's content hash. Call before
`export_asset_to_godot`, which requires the texture already on disk.

**`validate_asset(asset_id: str) -> ValidationReport`**
Run every applicable rule against the current spec (re-rendering in memory; no prior
`render_asset` required; writes nothing to disk). `report.blocking is True` if any
finding has `severity == "error"`.

**`generate_preview(asset_id, fmt=None, scale=1, dry_run=False) -> PreviewResult`**
One GIF/WebP per `(animation, direction)`, under `build/<asset_id>/`. Raises for
terrain. `fmt` defaults to the asset's `export.preview_format`. Always re-renders
(not cached the way `render_asset` is).

**`export_asset_to_godot(asset_id: str, dry_run: bool = False) -> GodotManifest`**
Build `build/godot/<asset_id>.forge.json`. Idempotent (re-running against an unchanged
spec overwrites with byte-identical content). Precondition: `render_asset` (or
`build_asset_family`) must have run first — raises otherwise, naming the missing step.

**`build_asset_family(force: bool = False) -> BuildReport`**
Render + preview + export every asset in the project in one call, skipping assets
already up to date unless `force=True`. Idempotent; returns which assets built,
which failed or carry blocking findings, and the total finding count. Run before a
Godot import pass to make sure every asset's artifacts exist.

**`get_validation_report(asset_id: str) -> ValidationReport`**
The most recently *persisted* validation report for the asset (read from its revision
history), or a fresh `validate_asset` run if none exists. Cheaper than `validate_asset`
when nothing changed since the last edit; call `validate_asset` directly for a
guaranteed up-to-date check.

*Implementation note:* `build/<id>/manifest.json` (`AssetManifest`) only persists a
`ValidationSummary` (counts), never the findings list — it cannot serve as "the last
persisted report". The only place a full `ValidationReport` is ever written to disk
is `RevisionRecord.validation`. "Last persisted report" is read here as the newest
revision that carries one; if `AssetManifest` ever grows a persisted full report,
that should be preferred instead.

### Revisions

**`apply_asset_operation(asset_id, op: OperationSpec, timestamp: str, dry_run=False) -> RevisionRecord`**
Apply one operation (`resize_region`, `translate_region`, `recolor_region`,
`set_frame_duration`, `add_frame`, `remove_frame`, `set_region_visibility` — full
catalogue in `docs/revisions.md`) and record it as a new, invertible revision. Call
`list_operations` first to see param names. Not idempotent — every call appends a new
revision, even a repeat of an identical operation. `dry_run=True` returns the record
that *would* be written, without touching the spec file or the revision log.

```
apply_asset_operation(
    asset_id="hero",
    op={"name": "translate_region", "params": {"region": "block", "offset": [1, 0]}},
    timestamp="2026-08-05T12:00:00Z",
)
```

**`compare_revisions(asset_id, revision_a, revision_b) -> RevisionDiff`**
Diff between two revisions: operations applied between them (in order) plus the union
of regions/frames/directions they touched. Both ids must already exist (see
`list_revisions`). Order-independent.

**`list_revisions(asset_id: str) -> list[RevisionRecord]`**
Every revision for the asset, oldest first, each with its operation, inverse,
before/after hashes, and (if computed at the time) a validation report.

**`list_operations() -> list[OperationInfo]`**
Name, description, and param names for every operation `apply_asset_operation`
understands. Doesn't depend on the project root or any asset — call before
constructing an `OperationSpec` so params line up.

### Inspection / seams

**`inspect_asset(asset_id: str) -> AssetInspection`**
Structured overview: animations (frame counts, durations, events), regions
(anchor/layer/shape-count/protected), anchors, palette size, revision count and head
revision id, output paths from the last build. Cheaper than `get_asset` when an
overview is enough — no full shape/region geometry.

**`test_seams(asset_id: str) -> SeamReport`**
Terrain only (raises for character/enemy/prop). Renders every tile, checks self- and
cross-tile edges, writes a seam-map PNG. `worst_mismatch` is the largest
mismatched-pixel count across every checked edge; `0` means every tile tiles cleanly
against itself.

### References / style profile

**`get_style_profile() -> StyleProfile`**
The project's style profile: perspective, palette tendencies, outline style, light
direction, and similar fields, plus reference provenance. Creates an empty profile on
first call. Read this before generating or editing assets, to stay consistent with
the established style.

**`update_style_profile(perspective=None, pixel_density=None, ..., provenance=None) -> StyleProfile`**
Shallow-merges the given fields (every parameter defaults to `None`, meaning "leave
unchanged") and appends de-duplicated `provenance` entries (deduped by
`source_path` + `role`). Call `scaffold_references` first if `references/` doesn't
exist, and actually look at the image files under
`references/{approved,inspiration,...}/` before calling this — see `docs/references.md`
for the full workflow this tool is designed around.

```
update_style_profile(
    outline_style="1px dark outline, no anti-aliasing",
    light_direction="top-left",
    provenance=[{
        "source_path": "references/approved/hero_ref.png",
        "role": "approved",
        "notes": "outline treatment reference",
    }],
)
```

**`scaffold_references() -> list[str]`**
Ensure `references/{approved,inspiration,palettes,animation,rejected}` all exist
(each with a README stating the no-tracing policy), and return their project-relative
paths. Idempotent — never overwrites a file already there. Call once early in a
project.

## Worked agent workflow

Create a character, render, validate, fix a finding, export:

```
create_asset(asset_type="character", asset_id="scout")

# Look, then commit to a starting point
spec = get_asset(asset_id="scout").model_dump(mode="json")
# ... edit spec: add regions/anchors/animations directly, or build it up with
#     apply_asset_operation calls instead ...
update_asset_spec(asset_id="scout", spec=spec, timestamp="2026-08-05T12:00:00Z")

render_asset(asset_id="scout")
report = validate_asset(asset_id="scout")

# report.blocking is True: an ANI002 finding says the "feet" anchor drifted in "walk"
apply_asset_operation(
    asset_id="scout",
    op={"name": "translate_region", "params": {"region": "shadow", "offset": [0, 0]}},
    timestamp="2026-08-05T12:05:00Z",
)
# ... or, more likely, fix the offending frame's transform directly via
#     update_asset_spec, then re-validate ...

report = validate_asset(asset_id="scout")
assert not report.blocking

export_asset_to_godot(asset_id="scout")  # requires render_asset to have run first
```

For a project with several assets, prefer `build_asset_family()` once at the end
instead of render/preview/export-ing each asset individually.
