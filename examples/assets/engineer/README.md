# `engineer.yaml` — four-direction sci-fi character

A worked, hand-authored example for `asset.type: character`, built to validate
clean against the real rules in `src/pixel_forge/validation/`.

## What it demonstrates

- **64x64 canvas**, `directions: [south, west, east, north]`, with
  `mirror: {east: west}` — `east` is never hand-authored; it is produced by
  mirroring the rendered `west` frames (`Canvas.mirror_x`).
- **Anchors**: `feet`, `torso`, `head`, `upper_back`, `right_hand`. `torso` is
  a separate anchor from `feet` on purpose, so the walk bob can move the
  whole body region vertically without disturbing the foot/shadow anchor
  that `ANI002` checks.
- **Regions** with the full shape DSL (`rect`, `ellipse`) across five
  layers: `shadow` (0), `backpack` (10, anchored `upper_back`), `body` (20,
  anchored `torso`), `helmet` (40, anchored `head`), `weapon` (50, anchored
  `right_hand`).
- **`direction_overrides`**: `south`/`west` hide the backpack (camera sees
  the front); `north` hides the weapon and shows the backpack (camera sees
  the back) — a real per-direction visual difference driven entirely by
  `RegionTransform.visible`, since the underlying region geometry is shared
  across all non-mirrored directions by design (see "Schema limitation
  hit" below).
- **Animations**: `idle` (4 frames, 160ms, loop) and `walk` (4 frames,
  100ms, loop) both drive a 1px vertical bob via explicit per-frame `body`
  offsets; `attack` (4 frames, loop: false) swings the `weapon` region and
  fires a `contact` event on the strike frame.
- **A 5-colour palette** (well under the 24-colour `validation.palette_limit`):
  one dominant suit colour, one shared metal tone for helmet/backpack/weapon,
  a glow accent, a gold accent, and the ground shadow.

## Building it (once the CLI lands)

```sh
pixel-forge render examples/assets/engineer/engineer.yaml
pixel-forge validate examples/assets/engineer/engineer.yaml
pixel-forge preview examples/assets/engineer/engineer.yaml --animation walk
```

Until then, the pipeline is driven directly:

```python
from pixel_forge.domain.loader import load_asset_doc
from pixel_forge.rendering.local import render_asset_frames

doc = load_asset_doc(Path("examples/assets/engineer/engineer.yaml"))
frames = render_asset_frames(doc)  # {(animation, direction, index): Canvas}
```

See `tests/end_to_end/test_character_example.py` for the full
load -> render -> validate flow, including the mirror-correctness check.

## Validation result

Zero errors, zero warnings (one `info`-level `PIX010` finding: this asset
declares no palette colour `role`/`ramp`, so the lighting-consistency
heuristic has no metadata to check and reports that fact rather than a
pass/fail — this is expected and not a warning).

## Design notes worth flagging

- **Baseline stability (`ANI001`)**: `baseline_y: 57` is held constant by
  the `shadow` region, which is never offset by any frame or override. Every
  other region's lowest opaque row stays strictly above 57 in every pose, so
  the measured lowest row always equals the declared baseline regardless of
  the walk bob.
- **`PIX007` (silhouette-outline heuristic)**: the palette was deliberately
  kept to 5 colours, with every accent colour (`visor_glow`, `accent`)
  placed with a >=2px margin inside its parent shape, so no minority colour
  ever reaches the silhouette's outer edge. The one colour that legitimately
  touches the edge in a small share (`shadow`, the contact shadow) was sized
  so its edge contribution clears the heuristic's 10% minority threshold in
  every frame, rather than trying to out-argue the heuristic with a
  justification. No warnings remain to justify.
- **Schema limitation hit**: the schema has exactly one `regions` map and
  one `anchors` map per document, shared by every non-mirrored direction —
  there is no way to give `south`/`west`/`north` genuinely different body
  silhouettes short of duplicating the whole document per direction (which
  the schema doesn't support either). The only sanctioned per-direction
  differentiation mechanism is `direction_overrides` (offset/visible/
  color_swap/scale_size deltas), which is enough for toggling equipment
  visibility (as done here) but cannot express "west shows the character in
  profile" the way a hand-drawn 4-direction sprite sheet normally would.
  Worth a note for anyone expecting per-direction art from this MVP schema.
