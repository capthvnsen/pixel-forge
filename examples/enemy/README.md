# `crawler.yaml` — two-direction combat enemy

A worked, hand-authored example for `asset.type: enemy`, exercising the
`combat:` block and validating clean against the real rules in
`src/pixel_forge/validation/`.

## What it demonstrates

- **64x64 canvas**, `directions: [east, west]`, with `mirror: {west: east}` —
  `west` is never hand-authored; it is produced by mirroring the rendered
  `east` frames.
- **Anchors**: `ground` (shadow), `core` (shell + legs), `eye` (a single
  "front-facing" eye that ends up on the correct mirrored side on `west`).
- **Six animations**: `idle`, `move` (both looping), `telegraph`, `attack`,
  `impact`, `death` (all non-looping). `death.loop` is explicitly `false`.
- **Events on the meaningful frames**: `telegraph_start` on telegraph's
  first frame, `hitbox_on`/`hitbox_off` bracketing the hit window in
  `attack`, `death_complete` on death's last frame — all declared via
  `FrameSpec.events`.
- **`combat:`** block: `telegraph_animation: telegraph`,
  `death_animation: death`, `hit_frames: {attack: [2]}` (frame index 2 is
  where the hit registers; `hitbox_on`/`hitbox_off` bracket it at indices
  2 and 3).
- **`color_swap`** (eye glows a warning colour during `telegraph`/`attack`,
  the shell darkens during `death`), **`scale_size`** (the eye grows during
  `idle`'s breathing pulse and `telegraph`'s charge-up), and **`visible`**
  (the eye closes partway through `death`) — all three `RegionTransform`
  features, deliberately kept off the `offset` axis so they carry zero
  baseline/anchor-drift risk (see design notes below).

## Building it (once the CLI lands)

```sh
pixel-forge render examples/enemy/crawler.yaml
pixel-forge validate examples/enemy/crawler.yaml
pixel-forge preview examples/enemy/crawler.yaml --animation attack
```

Until then, the pipeline is driven directly:

```python
from pixel_forge.domain.loader import load_asset_doc
from pixel_forge.rendering.local import render_asset_frames

doc = load_asset_doc(Path("examples/enemy/crawler.yaml"))
frames = render_asset_frames(doc)  # {(animation, direction, index): Canvas}
```

See `tests/end_to_end/test_enemy_example.py` for the full
load -> render -> validate flow, including the `combat` block and event
assertions.

## Validation result

Zero errors, zero warnings (one `info`-level `PIX010` finding, same as the
character example: no palette colour declares a `role`/`ramp`, so the
lighting-consistency heuristic reports that it has nothing to check).

## Design notes worth flagging

- **Baseline stability (`ANI001`)**: `baseline_y: 40` is held by the
  `shadow` region (never offset, always visible), mirroring the same
  pattern used in `engineer.yaml`. The `move` bob only ever moves the body
  region *up* (offset `[0, -1]`), and the legs' resting row (39) already
  sits one row above the shadow's row (40), so the measured lowest row is
  40 in every frame of every animation.
- **Eye placement and `PIX007`**: the first draft placed the `eye` region's
  bounding box close enough to the body ellipse's edge that part of its
  own boundary reached the silhouette's outer edge, tripping the
  minority-edge-colour heuristic (~4% share, under the 10% threshold) in
  every animation that used `eye_color`/`eye_warn`. Moving the `eye` anchor
  further inside the body ellipse's semi-axes (comfortably interior on both
  x and y) fixed it: the eye is now fully surrounded by opaque `body_main`
  pixels, so its colour never appears on the silhouette edge at all.
- **No offsets outside `move`**: `telegraph`/`attack`/`impact`/`death` only
  ever use `color_swap`/`scale_size`/`visible`, never `offset`. That's a
  deliberate simplification — it sidesteps `ANI003`/`ANI004` position-drift
  bookkeeping entirely for those animations, at the cost of a static-looking
  attack/death silhouette. A future revision could add a lunge/collapse
  offset; it would need explicit per-frame `body` transforms in every frame
  of that animation (`ANI004`) and would trip `ANI003` as a *warning*
  (not an error, since these animations are all `loop: false`) if the
  bbox centre-x moved more than 2px between frames.
