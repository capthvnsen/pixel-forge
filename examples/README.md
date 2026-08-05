# pixel-forge examples

A real pixel-forge project (`pixel-forge.yaml` + `assets/<id>/<id>.yaml`) with
one worked example per asset type. Build it, inspect the per-asset READMEs
below for what each one demonstrates, or use it as a template for a new
project.

## Assets

- **`assets/engineer/`** — `character`. Four directions with a mirrored
  `east`, layered regions with anchors, per-direction equipment visibility,
  and a walk-bob animation. See `assets/engineer/README.md`.
- **`assets/crawler/`** — `enemy`. Two directions with a mirrored `west`, the
  `combat` block (telegraph/death animation names, hit frames), and events on
  the meaningful frames. See `assets/crawler/README.md`.
- **`assets/beacon/`** — `prop`. A static base region, a region driven purely
  by per-frame transform offsets, a genuine visible/color_swap blink, and a
  `procedural` shader block. See `assets/beacon/README.md`.
- **`assets/forest_tileset/`** — `terrain`. Base tiles, a full 8-mask
  transition set, animated water, terrain sets, and a sample map, built to
  tile with zero seam mismatch. See `assets/forest_tileset/README.md`.

## Building it

`--root` is a per-command option (it comes after the asset id, not before the
command). All of the following have been run against this project and work:

```sh
uv run pixel-forge build-all --root examples
uv run pixel-forge validate engineer --root examples
uv run pixel-forge render forest_tileset --root examples
uv run pixel-forge export godot beacon --root examples
```

`build-all` renders, previews, and exports every asset into `examples/build/`
(git-ignored) and reports 0 blocking findings for all four.
