# CLI reference

`pixel-forge` (`src/pixel_forge/cli/main.py`, `cli/commands.py`) is a thin Typer
wrapper: every command calls exactly one `pixel_forge.api` function and renders the
returned pydantic model. No domain logic lives here.

## Global options

Global options go on `pixel-forge` itself, *before* the subcommand:

```
pixel-forge [--json/-j] [--quiet/-q] [--version] COMMAND [ARGS]...
```

| Option | Meaning |
|---|---|
| `--json` / `-j` | Emit the result as one JSON document on stdout (`model.model_dump_json(indent=2)`, or a JSON array for list-returning commands) instead of the human-readable text form. |
| `--quiet` / `-q` | Suppress stdout entirely (still respects the command's exit code). |
| `--version` | Print the version and exit immediately. |

Every subcommand that scopes to a project accepts `--root PATH` (default `.`);
`init`'s project path is a positional argument instead, since a project doesn't exist
yet to scope against.

Every subcommand that scopes to an asset accepts an `asset_id` positional argument.
It accepts either the bare id (`engineer`) or the spec-style path (`assets/engineer`,
trailing slash optional) — both normalise to the same id before reaching `api.py`.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | `validate`, `build`, and `build-all` exit `1` when the resulting `ValidationReport`/`AssetManifest`/`BuildReport` is blocking (any `error`-severity finding). The command still prints its normal output first. |
| `2` | Typer usage error: unknown command, unknown option, bad `--operation` name, malformed `--param`/`--field`/`--provenance` value. Standard Typer/Click behaviour, unrelated to `pixel_forge`. |
| `3` | Any `ForgeError` (asset not found, invalid path, schema error, operation error, export error, ...) or any other unexpected exception. `commands.guarded` catches these, prints the message to stderr, and never a traceback. |

## Commands

### `pixel-forge init PATH [--name NAME]`

Initialise a new project at `PATH` (positional, not `--root`). `--name` defaults to
`PATH`'s resolved directory name. Idempotent against an *identical* existing config;
raises if a project with a different config already exists there.

Text output: `initialised project '<name>' at <path>`. JSON: `ProjectConfig`.

### `pixel-forge new ASSET_TYPE ASSET_ID [--root ROOT] [--dry-run]`

`ASSET_TYPE` is one of `character`, `enemy`, `prop`, `terrain` (positional, no flag).
Creates the asset from the built-in starter template (`templates.py`), guaranteed to
render and validate with zero blocking findings. `--dry-run` computes the result
without writing the spec file.

Text output: id, type, frame count, spec hash, spec path, directions, animations.
JSON: `AssetSummary`.

### `pixel-forge list [--root ROOT]`

Every asset in the project. Text output: a table (`id`, `type`, `directions`,
`animations`, `frames`). JSON: `list[AssetSummary]`.

### `pixel-forge inspect ASSET_ID [--root ROOT]`

Structural overview: animations (frame counts, durations, events), regions
(anchor/layer/shape-count/protected), anchors, palette size, revision count/head, and
any output paths recorded by the last build. JSON: `AssetInspection`.

### `pixel-forge validate ASSET_ID [--root ROOT]`

Runs every applicable validation rule (re-rendering in memory; writes nothing).
Text output: `ValidationReport.to_text()`. JSON: `ValidationReport`. **Exits `1` if
blocking.**

### `pixel-forge render ASSET_ID [--root ROOT] [--force] [--dry-run]`

Renders frames, sprite sheet, and contact sheet (or, for terrain, tiles and an atlas)
under `build/<asset_id>/`. Cached against `content_hash(doc)` unless `--force`.
`--dry-run` reports what *would* happen without writing.

Text output: rendered/skipped/dry-run status, frame count, sheet/contact-sheet paths.
JSON: `RenderResult`.

### `pixel-forge preview ASSET_ID [--root ROOT] [--format {gif,webp}] [--scale N] [--dry-run]`

One animated preview per `(animation, direction)` pair (sprites only; raises for
terrain). `--format` defaults to the asset's own `export.preview_format`. `--scale`
is an integer nearest-neighbour upscale (`min=1`). Always re-renders (not cached like
`render`).

Text output: format, one line per `"{animation}_{direction}"` -> path. JSON:
`PreviewResult`.

### `pixel-forge export godot ASSET_ID [--root ROOT] [--dry-run]`

Builds `build/godot/<asset_id>.forge.json`. **Requires the asset's texture already on
disk** — raises `ExportError` if `render` (or `build`) hasn't run yet, naming exactly
which step to run first.

Text output: spec hash, per-texture path, animation names or tile count. JSON:
`GodotManifest`.

### `pixel-forge revise ASSET_ID --operation NAME [--param KEY=VALUE ...] [--protect NAME ...] [--timestamp ISO8601] [--root ROOT] [--dry-run]`

Applies one revision operation (see `docs/revisions.md` for the full catalogue) and
appends a new, invertible revision. `--operation` is validated against
`api.list_operations()` up front — an unknown name is a Typer usage error (exit `2`),
not a `ForgeError`.

`--param KEY=VALUE` is repeatable; each value is parsed as JSON first
(`json.loads`), falling back to the raw string if that fails — so
`--param offset=[1,0]` parses as a list, but `--param region=block` stays a bare
string. `--protect NAME` is repeatable (anchor or region names). `--timestamp`
defaults to the current UTC time in ISO-8601 (the CLI is the only place in the
codebase allowed to read the clock; `api.py` always takes a caller-supplied
timestamp).

Text output: revision id, operation, timestamp, hash transition, affected
regions/directions/frames. JSON: `RevisionRecord`.

### `pixel-forge update-spec ASSET_ID --file PATH [--timestamp ISO8601] [--root ROOT] [--dry-run]`

Replaces an asset's entire spec document with the YAML at `--file` and records the
change as a `replace_spec` revision — the same audit trail `revise` produces, but for
structural edits the operation DSL doesn't cover (adding a region, changing
directions, editing palette colours). Rejects a document whose `asset.id` doesn't
match `ASSET_ID`, one that fails schema validation, or one that touches a
`protected: true` region. `--timestamp` defaults to the current UTC time, same as
`revise`. The equivalent MCP tool is `update_asset_spec`.

Text output/JSON: same `RevisionRecord` shape as `revise`.

### `pixel-forge operations`

Lists every operation `revise` understands: name, params, description. No `--root`
(operation metadata doesn't depend on any project or asset). Text output: a table.
JSON: `list[OperationInfo]`.

### `pixel-forge revisions ASSET_ID [--root ROOT]`

An asset's revision history, oldest first. Text output: a table (`revision`,
`timestamp`, `operation`, `parent`). JSON: `list[RevisionRecord]`.

### `pixel-forge diff ASSET_ID REV_A REV_B [--root ROOT]`

Operations applied between two revisions (order-independent — pass either order) and
the union of regions/frames/directions they touched. JSON: `RevisionDiff`.

### `pixel-forge test-seams ASSET_ID [--root ROOT]`

Terrain only (raises for sprite asset types). Renders every tile and checks every
ordered pair's edges against `check_seams`, writing a seam-map PNG under
`build/<asset_id>/`. Output is one line per `(tile_a, tile_b, edge)` triple — for N
tiles that's `4N²` lines; verbose by design, meant for scripted consumption
(`--json`) or piping through `grep`/`sort` more than eyeballing directly on a large
tileset.

JSON: `SeamReport` (`results: list[SeamEntry]`, `worst_mismatch: int`,
`seam_map_path: str | None`).

### `pixel-forge build ASSET_ID [--root ROOT] [--force]`

Render + preview + export in one step, cached as a unit against the spec hash (a
manifest only counts as "complete" once it carries a `godot` output path — a bare
prior `render` doesn't satisfy the cache). Text output: spec hash, output paths,
validation counts. JSON: `AssetManifest`. **Exits `1` if blocking.**

### `pixel-forge build-all [--root ROOT] [--force]`

Runs `build` for every asset in the project; a `ForgeError` from one asset doesn't
stop the others (it's recorded in `failed` and the loop continues). Text output:
total assets/findings, per-asset ok/BLOCKED status, failed list. JSON: `BuildReport`.
**Exits `1` if any asset is blocking or failed.**

### `pixel-forge references init [--root ROOT]`

Scaffolds `references/{approved,inspiration,palettes,animation,rejected}/`, each with
a README carrying the no-tracing policy. Idempotent — never overwrites an existing
file. Output: the created/verified directory paths (project-relative), one per line
or a JSON array.

### `pixel-forge style show [--root ROOT]`

Prints the project's style profile, creating an empty one on first call. Text output:
one `field: value` line per non-empty field, plus a provenance count. JSON:
`StyleProfile`.

### `pixel-forge style set [--field KEY=VALUE ...] [--provenance PATH:ROLE[:NOTES] ...] [--root ROOT]`

Shallow-merges the given fields into the style profile (only passed fields change)
and appends de-duplicated provenance entries. `--field` values are parsed as JSON
first, same rule as `revise --param` — so a plain string value needs its own quotes
in most shells, e.g. `--field outline_style='"1px dark outline"'`. `--provenance`
format is `PATH:ROLE:NOTES` (`NOTES` optional; `ROLE` must be one of `approved`,
`inspiration`, `palette`, `animation`, `rejected`).

### `pixel-forge schemas export OUT_DIR`

Writes `<name>.schema.json` for every public pydantic model (see `docs/schema.md`'s
last section) into `OUT_DIR`. No `--root` — schema shape doesn't depend on a project.
Output: the written file paths.

## JSON output shape

`--json` always emits the same pydantic model the text renderer would have summarised,
either as a single `model_dump_json(indent=2)` document or (for list-returning
commands: `list`, `operations`, `revisions`) a JSON array of `model_dump(mode="json")`
objects. There is no separate "CLI JSON schema" — it is exactly the model documented
in `docs/schema.md`/`docs/revisions.md`/`docs/validation.md` for that return type.
`references init` and `schemas export` are the two exceptions: they return a bare list
of path strings, not a pydantic model.
