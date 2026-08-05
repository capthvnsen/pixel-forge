# `beacon.yaml` — animated prop example

A 32x32 sci-fi signal beacon, single direction (`south`). Exists to prove out the
`asset.type: prop` schema surface end to end: a region that never animates, a
region driven purely by per-frame transform offsets, a region driven by
visible/color_swap toggles, and a region driven by a `procedural` shader block
instead of baked keyframes.

## What it demonstrates

- **Static base** (`base` region, layer 0): pedestal foot + stalk + dome cap.
  Never referenced by any frame's `transforms`, so every frame renders it with
  the identity transform — provably identical pixels across every frame (see
  `tests/end_to_end/test_prop_example.py::test_static_base_region_is_identical_across_every_frame`).
- **Layered transform animation** (`moving_regions: [vane]`): the `vane`
  region's crossbar + stem never change shape; every frame instead supplies a
  `RegionTransform.offset` that swings it left/right. That's the whole
  mechanism — no shape re-authoring per frame.
- **Genuine blink** (`lamp` region): `idle` pulses it via `color_swap` between
  `lamp_bright`/`lamp_dim` (always visible, brightness only); `active` toggles
  `visible: true/false` outright — a real on/off blink, not just a colour
  change.
- **Procedural shader contract** (`procedural_regions: [glow]`): the `glow`
  region is never touched by any frame's `transforms` at all. Its animation
  is expressed as the `active` animation's `procedural:` block (`shader:
  energy_pulse`, `params`, `target_region: glow`) — schema + exporter
  contract only, since the local renderer doesn't execute shaders. The test
  suite asserts this block survives YAML parsing intact.
- Layout is deliberately non-overlapping: every vane/lamp/glow pixel across
  every frame stays inside `x=[11,22) y=[0,10)`, strictly above the base's
  own `x=[10,22) y=[10,30)` footprint. See the comment at the top of the YAML
  for the exact reasoning; that's what makes the "static region actually
  stays static" test meaningful rather than a coincidence.

## Godot mapping

- `regions` → four `Sprite2D`/`AnimatedSprite2D` layer nodes (or one node with
  a `SpriteFrames` per-region track), ordered by `layer`.
- `animations.idle` / `animations.active` → `AnimationPlayer` animations, one
  track per moving region (`vane`: position keys from `offset`; `lamp`:
  visibility + modulate keys from `visible`/`color_swap`).
- `animations.active.procedural` → a `ShaderMaterial` assigned to the `glow`
  node, with `shader` naming the `.gdshader` resource and `params` feeding its
  uniforms; `target_region` says which node gets the material.
- `moving_regions` / `procedural_regions` → informs the Godot exporter which
  `AnimationPlayer` tracks to bake (moving) vs. which nodes get a
  `ShaderMaterial` instead (procedural), per the plan's Task 9 contract.

## Validation

`report.blocking is False` — zero error-severity findings. Remaining findings:

| Rule | Severity | Count | Why it's expected |
|---|---|---|---|
| `ANI006` | warning | 4 | "Palette flicker": a colour present in frame N and N+2 but absent in N+1. This is exactly what an intentional per-frame blink/pulse looks like to a heuristic tuned to catch *accidental* flicker — the lamp is designed to alternate every frame in both animations, so this fires by construction, not by mistake. |
| `PIX007` | warning | 9 | "Minority outline colour": the vane/lamp are small, physically separate parts floating above the base, not a continuous silhouette with one ink outline. Their own colour is inherently a small share of the frame's total silhouette edge — that's a consequence of the prop's shape (a light and a vane sitting apart from the base), not a stray pixel. |
| `PIX010` | info | 1 | No palette colour declares a `role`/`ramp`, so the lighting-consistency heuristic has no metadata to check and reports that fact as `info` (not a warning, not blocking). Not applicable to a beacon with no directional key light. |
