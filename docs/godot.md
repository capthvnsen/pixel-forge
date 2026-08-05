# Godot plugin

The Pixel Asset Forge Godot plugin (`godot/addons/pixel_asset_forge/`) imports
`*.forge.json` manifests (as emitted by `pixel_forge.exporters.godot`, schema
`manifest_version: 1`) into native Godot resources: `SpriteFrames` for
character/enemy assets, `Animation` resources for prop assets, and `TileSet`
for terrain assets.

**Supported Godot version: 4.4.** The project declares `config/features =
PackedStringArray("4.4")` in `godot/project.godot` and `plugin.cfg` states the
same. The plugin was developed and verified against Godot `4.7.1.stable` on
this machine; it uses no APIs beyond what 4.4 ships (`TileMapLayer`,
`TileSetAtlasSource`, `SpriteFrames.add_frame` with a duration argument,
`Animation.TYPE_VALUE` tracks), so it is expected to run unmodified on 4.4.

## Install

1. Copy or symlink `godot/addons/pixel_asset_forge/` into the `addons/`
   directory of your own Godot project (or open `godot/` in this repo
   directly as a sample project — it already ships the plugin enabled).
2. Open the project in the Godot 4.4 editor.
3. Go to **Project > Project Settings > Plugins** and confirm "Pixel Asset
   Forge" is enabled (the sample `godot/project.godot` already enables it via
   `[editor_plugins]`).
4. A **Pixel Asset Forge** dock appears docked to the lower-left editor
   panel.

## Manifest directory convention

The plugin looks for `*.forge.json` files under a manifest directory,
default `res://forge/`, editable in the dock's directory field. Each
manifest's texture paths (`textures.<name>`) are resolved **relative to the
directory the manifest file itself lives in** — e.g. a manifest at
`res://forge/beacon.forge.json` with `"textures": {"atlas":
"beacon/atlas.png"}` expects the texture at `res://forge/beacon/atlas.png`.
This mirrors the golden fixture layout in
`tests/golden/fixtures/godot/{beacon,forest}.forge.json`.

Every import writes exclusively under `res://generated/<asset_id>/`. The
manifest validator rejects any `asset_id` that isn't a plain
`[A-Za-z0-9_-]+` token, so a manifest cannot cause a write outside that
directory. Reimporting the same `asset_id` overwrites only that asset's own
files, at the same resource paths, so scene references that point at them
keep working; it never touches another asset's directory or hand-authored
resources elsewhere in the project.

## Headless import

```bash
godot --headless --path godot --script res://addons/pixel_asset_forge/headless_import.gd -- --manifest-dir=<DIR>
```

`<DIR>` may be a `res://` path or a plain filesystem path (absolute, or
relative to the invoking shell — the wrapper below resolves it to absolute
before invoking Godot, since Godot changes its working directory to `--path`
before the script runs). Defaults to `res://forge` if omitted.

The shell wrapper handles locating the `godot` binary and produces a clean
SKIPPED notice (exit 0) when Godot isn't installed, so CI without Godot
doesn't fail:

```bash
tools/godot_headless_import.sh [MANIFEST_DIR]
```

Exits non-zero if Godot is installed and any manifest fails validation or
import; prints a per-manifest `[CREATED|UPDATED|UNCHANGED|FAILED]` line plus
any warnings/errors, and a final `N imported, M failed, T total` summary.

## Manual verification steps

1. Open `godot/` as a project in the Godot 4.4 editor. Success: the editor
   loads with no script errors in the Output panel and a "Pixel Asset Forge"
   dock is visible.
2. Create `godot/forge/` if it doesn't exist, and copy in a `.forge.json`
   manifest plus its texture(s) at the relative paths its `textures` field
   names (see "Manifest directory convention" above). Success: the manifest
   appears as a row in the dock listing its asset id, type, and a truncated
   `spec_hash`.
3. Click **Import** on that row. Success: the dock's import log shows a
   green `<path>: created` line with no `ERROR:` lines, and the FileSystem
   dock shows a new `res://generated/<asset_id>/` folder containing the
   resource(s).
4. Double-click the generated `.tres` in the FileSystem dock to open it in
   the Inspector. Success: for a character/enemy manifest, the `SpriteFrames`
   shows one animation per `<animation>_<direction>` key with the expected
   frame count; for a terrain manifest, the `TileSet` inspector shows a
   `TileSetAtlasSource` with tiles at the expected atlas coordinates and
   terrain sets under **Terrains**.
5. Re-click **Import** on the same row without changing the manifest.
   Success: the log now shows `<path>: unchanged`, and the files under
   `res://generated/<asset_id>/` are unchanged (same resource paths).
6. Edit the manifest's `spec_hash` field (simulating a spec change) and
   re-import. Success: the log shows `<path>: updated`.
7. Break the manifest on purpose — e.g. set `"manifest_version": 2` or point
   `textures.atlas` at a file that doesn't exist — and import it. Success:
   the row's import fails with a red `ERROR:` line in the dock naming the
   exact manifest path and field (e.g. `unsupported manifest_version 2 (this
   plugin supports version 1)`), and nothing is written under
   `res://generated/`.
8. Drag a generated `TileMapLayer` scene (`res://generated/<asset_id>/
   <asset_id>_sample_map.tscn`, only produced for terrain manifests with a
   `sample_map`) into the 2D viewport. Success: it renders the demo tile
   grid with nearest-neighbor (blocky, not blurry) filtering.
9. From a terminal, run `tools/godot_headless_import.sh godot/forge`.
   Success: prints the same per-manifest summary as the dock and exits 0.

## What is / is not automatically verified without Godot

`tests/end_to_end/test_godot_plugin.py` runs on every machine, with or
without Godot installed, and is the only automatic check that runs in CI
here. It verifies, statically, in Python:

- Every required plugin file exists and is non-empty.
- `plugin.cfg` declares the Godot 4.4 baseline.
- Every `.gd` file that reads a manifest field only reads fields that
  `pixel_forge.schemas.manifest.GodotManifest` actually produces (catching
  drift between the exporter schema and the plugin without needing to run
  either).
- The importer only ever constructs output paths under `res://generated/`.
- `tools/godot_headless_import.sh` is executable and exits 0 with a SKIPPED
  notice when `godot` is absent from `PATH`.
- A small Python re-implementation of `manifest_validator.gd`'s checks
  (required keys, `manifest_version`, `asset_type`, texture existence, atlas
  rect bounds) proves the two golden fixtures' *shape* is one the validator
  accepts (modulo the fixtures shipping no texture files — see below).

It does **not**, and cannot without Godot, prove that the GDScript actually
parses, that the Godot API calls used (`TileSet.add_terrain`,
`SpriteFrames.add_frame`, `Animation.track_insert_key`, ...) have the
signatures this code assumes, or that the resources it writes load correctly
back into Godot.

**This machine has Godot 4.7.1 installed**, so that stronger verification
was in fact done by hand, not left to assumption: the headless script was
run against `tests/golden/fixtures/godot/{beacon,forest}.forge.json`
directly (see below), and separately against a scratch directory containing
copies of those two manifests plus a synthetic character manifest, each with
a hand-generated tiny PNG standing in for the real exported atlas texture.
That run produced `SpriteFrames`, `Animation`, and `TileSet` resources for
all three asset types, an inspected-by-hand `.tres` dump for each confirming
correct FPS derivation, keyframe times/values, terrain peering bits, and a
demo `TileMapLayer` scene with `texture_filter` set to Nearest — and running
the import a second time with an unchanged `spec_hash` correctly reported
`unchanged`, then `updated` after changing it. That scratch directory isn't
part of this repo (the task's file allowlist for this plugin doesn't include
adding fixture textures), so it isn't repeatable from the checked-in tests —
only the honest, hand-verified account above stands in for it.

Running `tools/godot_headless_import.sh tests/golden/fixtures/godot` as
committed **will report both fixtures as FAILED**: the fixtures directory
ships no `atlas.png` next to either `.forge.json` (only the JSON manifests
are golden fixtures; the PNGs they reference were never committed), so the
validator correctly refuses to import a manifest whose texture doesn't exist
on disk, and says exactly so
(`textures.atlas references 'beacon/atlas.png' which does not exist at
.../beacon/atlas.png`). That is the validator's "display useful import
errors" requirement working as intended, not a bug — this doc calls it out
so a future run of that exact command isn't mistaken for a regression.

## Known limitations

- **Texture filtering is not a Godot 4 import setting.** Godot 3's
  per-texture nearest/linear import flag was removed in Godot 4; filtering
  is now a `CanvasItem.texture_filter` (per-node) or project-default
  (`rendering/textures/canvas_textures/default_texture_filter`) concern.
  `godot/project.godot` sets that project default to Nearest (`0`), and the
  plugin explicitly sets `texture_filter = TEXTURE_FILTER_NEAREST` on the
  `TileMapLayer` nodes it builds. It cannot force nearest filtering on a
  `Sprite2D`/`AnimatedSprite2D` a game developer builds themselves around a
  generated `SpriteFrames`/`Animation` — that node needs to leave
  `texture_filter` at its default (inherit) or set it to Nearest itself.
  "No mipmaps" *is* still a real import parameter
  (`mipmaps/generate`); the importer patches it in an existing `.import`
  file when one is already present, but deliberately does not fabricate a
  `.import` file for a texture the editor has never imported (Godot 4.3+'s
  import file format carries a resource `uid` a hand-rolled file risks
  getting wrong). This has no effect on the generated resources' own
  correctness — they read the source PNG's pixels directly via
  `Image`/`ImageTexture`, not through the editor's cached import.
- **`SpriteFrames` has no standalone `set_frame_duration` setter** in the
  Godot 4.7 API this was verified against (only `get_frame_duration`, plus
  `add_frame`/`set_frame` which take the duration alongside the texture) —
  confirmed by querying `ClassDB` directly rather than assumed. The importer
  passes the derived per-frame duration to `add_frame` at creation time
  instead of a separate call.
- **A terrain tile's own "I am 100% terrain X" assignment is not exported**
  by the manifest schema — only `tileset.terrain_bits` (edge/corner peering
  data) is. The importer sets `TileData.terrain_set` for every tile listed
  in a `terrain_sets[name].tiles`, and applies whatever peering bits
  `terrain_bits` provides for that tile, but leaves `TileData.terrain`
  unassigned (`-1`) otherwise — see the `forest` fixture's `grass` tile,
  which gets a `terrain_set` but no peering bits, matching its absence from
  `terrain_bits`. A human may need to confirm base-tile terrain assignment
  for wholly uniform tiles in the terrain painter.
- **`animated_tiles` frames that reuse existing, non-adjacent named tiles
  cannot always be represented.** Godot's `TileSetAtlasSource` animation
  model requires an animated tile's frames to occupy a single contiguous
  horizontal strip of atlas cells starting at its own coordinates, entirely
  inside the atlas texture. The neutral manifest schema instead lists
  arbitrary already-named tile ids as frames (see the `forest` fixture's
  `water_flow`, which cycles between the pre-existing `grass` and `dirt`
  tiles). When a tile's frames don't form such a strip, the importer leaves
  that tile static and emits a warning naming the tile and why, rather than
  calling a Godot API that would misbehave or throw an engine-level error.
- **Props ignore the `sprite_frames` payload** except to look up an
  animation's `loop` flag (by matching `sprite_frames` keys prefixed
  `<animation>_`) for the corresponding `Animation` resource's
  `loop_mode`, since `animation_player.tracks` alone carries no loop
  signal. Per the task spec, `SpriteFrames` resources are only built for
  character/enemy asset types; a prop's `sprite_frames` sheet (if the
  exporter emits one) is not currently turned into its own resource.
