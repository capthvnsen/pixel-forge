# Pixel Forge

A headless, AI-native pixel-art asset production toolkit for Godot 4.

It is a structured production system, not an image generator and not a drawing app.
You describe an asset as a YAML spec (palette, anchors, layered regions, a small shape
DSL, per-frame transforms). Pixel Forge renders it deterministically into sprite
sheets and previews, checks it against an automated rule set, records every edit as a
reversible revision, and exports it into Godot 4 through an editor plugin. No
hand-authored `.tres` or `.tscn` files, ever.

The point: **the editable source of an asset is a structured document, not a PNG.**
That is what makes an AI agent able to edit it precisely ("widen the backpack by two
pixels across all directions without moving the feet") instead of regenerating the
whole thing and hoping.

## Using this with an AI agent

This repo is built to be handed to a coding agent. Point your agent at it and it can
author, render, validate, revise, and export assets on its own.

### Step 1: give your agent this prompt

```
Read AGENTS.md in this repository, then help me create a pixel art asset with
Pixel Forge. Before writing any YAML, interview me using the question tables under
"Using this with an AI agent" in README.md. Ask only the questions that apply to my
asset type, and propose a sensible default for each one so I can just say "yes" to
the ones I do not care about. Once I have answered, write the spec, render it, run
validation, and fix anything blocking before showing me the result. Then show me
the contact sheet and the preview GIF so I can give you notes.
```

### Step 2: your agent asks you these questions

This is the checklist. A good agent proposes a default for every line so you only
have to answer the ones you actually care about.

**Every asset (ask these first)**

| Question | Why it matters | Common default |
|---|---|---|
| What is this asset, in one sentence? | Drives region breakdown | none |
| Which type: character, enemy, prop, or terrain? | Picks the schema | character |
| Canvas size in pixels? | Fixed per asset, hard to change later | 32x32 or 64x64 |
| What is the camera angle? | Recorded as `perspective` | three-quarter top-down |
| Do you have a palette, or should one be proposed? | Every colour must be declared up front | propose one |
| Maximum colour count? | Enforced by rule `PIX005` | 24 |

**Characters and enemies**

| Question | Why it matters | Common default |
|---|---|---|
| Which facing directions? | Drives the whole sheet size | south, west, east, north |
| Can east be a mirror of west? | Halves the work, exercises `mirror` | yes |
| Which animations, and how many frames each? | idle, walk, attack, and so on | idle 4, walk 4, attack 4 |
| How fast should each animation run? | Per-frame milliseconds | 160ms idle, 100ms walk |
| Which row is the ground line? | `baseline_y`, kept pixel-stable | 2 to 6 px above the bottom |
| What attaches to this asset, and where? | Becomes named anchors | feet, head, weapon hand |
| Which of those must never move when you edit? | Becomes protected anchors | feet, weapon hand |
| Which parts are separate layers? | Becomes named regions you can edit alone | shadow, body, head, held item |

**Enemies also**

| Question | Why it matters | Common default |
|---|---|---|
| Does it telegraph before attacking? | Fairness cue, gets its own animation | yes |
| Does it have a hurt reaction and a death? | Extra animations, death does not loop | yes |
| On which frames does damage land? | Becomes frame events for your game code | contact frame of attack |

**Animated props**

| Question | Why it matters | Common default |
|---|---|---|
| What stays completely still? | The static base region | the base |
| What moves, and how? | Bob, spin, swing, or open | one moving part |
| Does anything blink or pulse? | Visibility toggles or colour swaps | a lamp |
| Should any effect be a Godot shader instead of frames? | Energy pulses, shimmer, holograms | one shader effect |

**Terrain and tilesets**

| Question | Why it matters | Common default |
|---|---|---|
| Tile size? | Fixed for the whole set | 16x16 |
| Which terrain types? | grass, dirt, stone, water | grass and dirt |
| Which pairs need transitions between them? | Generates the 8 edge and corner masks | every adjacent pair |
| Any animated tiles? | Water, lava, and similar | water, 3 frames |
| Which tiles block movement or sight? | Collision, navigation, occlusion hints for Godot | water blocks movement |
| Do you want a sample map to look at? | A small demo scene proving it tiles | yes, 8x8 |

**Quality bar (ask once, applies to everything)**

| Question | Why it matters | Common default |
|---|---|---|
| Must the feet and anchors stay pixel-stable across frames? | Stops sprites from sliding | yes |
| Is antialiasing allowed? | Pixel art usually says no | no |
| Which Godot version? | Plugin baseline | 4.4 |

### Step 3: your agent builds it

With the answers, the agent writes `assets/<id>/<id>.yaml` and runs:

```bash
uv run pixel-forge render <id> --root .      # spec to pixels
uv run pixel-forge validate <id> --root .    # exit code 1 if anything is blocking
uv run pixel-forge preview <id> --root .     # animated GIF to look at
uv run pixel-forge build <id> --root .       # all of the above plus the Godot manifest
```

`validate` is the feedback loop. It returns machine-readable findings with a rule id,
a severity, the exact frame and region at fault, a measurement, and a suggested fix,
so the agent can correct the spec and re-run without asking you anything. Show your
agent the contact sheet or the preview GIF and it can iterate on your notes.

### Step 4: you ask for changes in plain language

Edits are semantic operations on named regions, not image edits. Ask for something
like "widen the backpack by two pixels everywhere but do not move the feet or the
weapon hand", and the agent runs:

```bash
uv run pixel-forge revise engineer \
  --operation resize_region --param region=backpack --param 'delta=[2,0]' \
  --protect feet --protect weapon --root examples
```

Every edit is recorded with a revision id, before and after hashes, and an inverse, so
it can be reversed and diffed. The full operation catalogue is in `docs/revisions.md`.

## Requirements

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/)
- Godot 4.4+ for the editor plugin. Not needed to author, render, validate, or revise
  assets, only to import them into a Godot project.

## Install

```bash
git clone https://github.com/capthvnsen/pixel-forge.git
cd pixel-forge
uv sync
uv run pixel-forge --version
```

Full command reference: `docs/cli.md`.

## Quick start

Every block below is real output from these exact commands.

```console
$ uv run pixel-forge init ./demo --name demo
initialised project 'demo' at /path/to/demo

$ uv run pixel-forge new character hero --root ./demo
hero (character): 2 frame(s), spec_hash=2e44719c5f6f445d...
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

$ uv run pixel-forge build hero --root ./demo
hero (character): spec_hash=2e44719c5f6f445d...
  contact_sheet: build/hero/hero_contact.png
  sheet: build/hero/hero_sheet.png
  godot: build/godot/hero.forge.json
  validation: 0 error(s), 0 warning(s)
```

`build` is render plus preview plus export in one command. Both `render` and `build`
are cached against the spec's content hash, so re-running after a no-op edit does
nothing (`skipped: true`) unless you pass `--force`. Rendering is deterministic:
the same spec produces byte-identical PNGs on every run and on every machine.

Add `--json` before any subcommand for structured output instead of text.

## The worked examples

`examples/` is a real, buildable project covering every asset type. See
[`examples/README.md`](examples/README.md) for the gallery with rendered previews.

- **`engineer`** (character): four directions with east mirrored from west, idle, walk
  and attack, stable feet, attachment anchors, per-direction equipment visibility.
- **`crawler`** (enemy): idle, move, telegraph, attack, impact, death, with frame
  events for hitboxes and combat metadata.
- **`sporeling`** (enemy): the full combat state machine across three directions, 60
  frames, squash-and-stretch through size deltas, events on the frames that matter.
- **`beacon`** (prop): a static base, a moving vane, a blinking lamp, and one
  procedural shader effect exported as Godot metadata.
- **`rune_chest`** (prop): a pixel-identical static base, a lid animated purely by
  region offsets, and a rune that pulses via palette swaps plus a shader block.
- **`forest_tileset`** (terrain): grass, dirt, all eight transition masks, animated
  water, adjacency metadata, seam tests, and a sample map.

Each has its own README explaining what it demonstrates and why its remaining warnings
are expected.

```bash
uv run pixel-forge build-all --root examples
```

```
built 6 asset(s), 146 finding(s) total
  beacon: ok
  crawler: ok
  engineer: ok
  forest_tileset: ok
  rune_chest: ok
  sporeling: ok
```

## Importing into Godot

The sample project at `godot/` ships the `pixel_asset_forge` plugin. Point its dock at
a directory of `*.forge.json` manifests (produced by `export godot` or `build`) and
click Import, or run it headlessly:

```bash
tools/godot_headless_import.sh [MANIFEST_DIR]
```

The plugin builds `SpriteFrames`, `AnimationPlayer` animations, `TileSet` with terrain
peering bits and animated tiles, and a sample `TileMapLayer`, all through Godot's own
APIs. It writes only under `res://generated/<asset_id>/`, never touching your own
resources, and a reimport updates in place so existing scene references keep working.
Full instructions and manual verification steps: `docs/godot.md`.

## Running the MCP server

Agents can drive the whole toolkit through MCP instead of the CLI. Full tool
reference and a worked agent workflow: `docs/mcp.md`.

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

The project root is fixed once at server startup and is never a per-tool parameter, so
a calling agent cannot point any tool outside the project it was launched against.
There is no shell tool and no arbitrary file access.

## Architecture

```
schemas       pydantic models: spec, palette, animation, revisions, manifests,
              validation report, style profile
   |
domain        paths and project lifecycle, palette resolution, geometry,
              content hashing, YAML I/O   (pure, no framework deps)
   |
animation     spec -> resolved (direction x animation x frame) expansion
   |
rendering / validation / preview / revisions / exporters.godot
   |   shape DSL -> pixels (RenderBackend Protocol seam)
   |   rule engine: PIX0xx / ANI0xx / TIL0xx
   |   deterministic GIF and WebP writers
   |   operation registry + append-only revision log
   |   AssetDocUnion -> neutral *.forge.json manifest
   |
api.py        the one service layer: pydantic in, pydantic out, no printing,
              no sys.exit, no clock reads
   |
  +-- cli/    Typer app: one function per command, calls exactly one api function
  +-- mcp/    MCP server: one tool per api function, returns the result unchanged
```

The CLI and the MCP server are thin renderers of the same calls, which is what
guarantees they behave identically. See `docs/adr/0001-architecture.md` for why this
shape was chosen and what it costs. If you are a coding agent editing this repository,
read `AGENTS.md` first.

## Known limitations

Stated honestly, read against the code:

- **Heuristic validation rules are untuned against real art.** `PIX006` through
  `PIX010`, `ANI005`, `ANI006`, `ANI008`, and `TIL007` use thresholds chosen to pass
  the shipped examples cleanly, not validated against a broad corpus of
  hand-drawn pixel art. Expect false positives on busy or organic art and false
  negatives on subtler mistakes. Full rule table: `docs/validation.md`.
- **No generative render backend ships.** Two backends exist: the local
  deterministic shape-DSL renderer, and `ExternalFrameBackend`, which loads pinned
  PNGs produced elsewhere (see `source:` in `docs/schema.md`). The `RenderBackend`
  Protocol remains the seam for a future generative-image or vision-model backend,
  but nothing here calls a model.
- **Per-direction art is transform overrides, not independent artwork.** A sprite has
  one shared `regions` map. `direction_overrides` can change visibility, offset,
  colour, and size per direction, but cannot give two directions genuinely different
  silhouettes the way independently drawn art would. An asset that declares `source:`
  sidesteps this entirely, since each direction is its own file.
- **Frame transforms are shared by every direction.** `AnimationSpec.frames[].transforms`
  has no direction dimension and `direction_overrides` has no frame dimension, so all
  directions of one animation replay identical motion. A sixteen-direction walk has one
  stride, which reads correctly in profile and wrongly head-on.
- **`scale_size` applies uniformly to every shape in a region**, so the smallest shape
  caps how far the whole region can be scaled before hitting the 1x1 floor.
- **Revision operations apply to sprite assets only, not terrain.** Terrain specs must
  be edited by hand or replaced wholesale via `update_asset_spec`.
- **The toolkit performs no image analysis of reference art.** It scaffolds reference
  directories and stores structured style judgements, but a vision-capable agent or a
  human has to look at the references and write the style profile. See
  `docs/references.md`, which also carries the no-tracing policy.
- **Rollback is implemented but not yet exposed.** `revert_revision` is tested and
  works, but is not wired into the CLI or MCP surface yet, so revisions are currently
  recorded and diffable but not one-command reversible.
- **A few schema fields are accepted but unused.** `ExportOptions.godot`,
  `ProjectConfig.default_palette`, and `OperationSpec.targets` are forward-looking
  surface not yet wired to behaviour.
- **`test-seams` output grows quadratically.** For N tiles it prints 4N² lines. Use
  `--json` for anything but a small tileset.
- **The example art is deliberately simple.** Technical completeness was the goal, not
  visual sophistication. The attack animation in particular reads much like idle.
- **Verified on Godot 4.7.1.** The declared 4.4 baseline uses no newer APIs knowingly,
  but has not been independently tested on a 4.4 binary.

## Development

```bash
uv run pytest                 # 429 tests
uv run mypy                   # strict, clean
uv run ruff check src tests
uv run ruff format --check src tests
```

## License

MIT
