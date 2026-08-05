# rune_chest (animated prop)

A chest with a glowing rune, 32x32, one direction, two animations.

<img src="../../previews/rune_chest_opening.gif" width="160" alt="rune chest opening">

<img src="../../previews/rune_chest_contact.png" width="560" alt="rune chest contact sheet">

## What it demonstrates

- **A genuinely static base.** The `base` region appears in no animation's
  `transforms`, so it is pixel-identical in all eight frames. Only the lid and the rune
  ever change. That separation is what makes the exported `AnimationPlayer` track list
  contain exactly one entry instead of one per region.
- **Layered transform animation.** The lid opens through per-frame `offset` values
  alone. No frame of the lid was redrawn.
- **Pulsing through palette swaps.** The rune breathes by swapping `rune_dim` for
  `rune_bright` per frame, rather than by drawing two versions of the shape.
- **Procedural animation metadata.** The `idle` animation carries a `procedural` block:

  ```yaml
  procedural:
    shader: energy_pulse
    target_region: rune
    params: { speed: 0.6, intensity: 0.35, hue_shift: 0.0 }
  ```

  This is not rendered into pixels. It is passed through to the Godot manifest so the
  glow can be a real shader in-engine, where a continuous effect belongs, while the
  four-frame swap stays as a fallback and as something you can see in the contact
  sheet.

## What Godot receives

The exporter emits one `AnimationPlayer` track, correctly skipping the regions that
never move:

```
animation_player tracks: ['opening/lid']
procedural: {"idle": {"shader": "energy_pulse", "target_region": "rune",
                      "params": {"speed": 0.6, "intensity": 0.35, "hue_shift": 0.0}}}
```

The plugin turns that into `generated/rune_chest/rune_chest_opening.anim.tres` with a
`lid:position` track, plus a meta resource carrying the baseline, pivots, events and
the source spec hash.

## Design note

The lid originally used `scale_size` to squash as it opened. Its `wood_dark` trim strip
is only 2px tall, so the renderer refused:

```
region 'lid' shape #1: scale_size (0, -2) would shrink size (20, 2) to (20, 0);
minimum is 1x1 per dimension
```

The offset alone reads as opening, so the scale was dropped rather than padding the
strip to work around the check.

## Validation

0 errors, 5 warnings, not blocking.

| Rule | Count | Why it is acceptable |
|---|---|---|
| `PIX007` | 5 | Heuristic: the iron banding and the dark trim each own a small share of the silhouette edge. They are deliberate details on a small sprite, not stray pixels. |
| `PIX010` | 1 | Info. No palette colour declares a lighting `role` or `ramp`. |

## Build it

```sh
uv run pixel-forge render rune_chest --root examples
uv run pixel-forge validate rune_chest --root examples
uv run pixel-forge preview rune_chest --root examples --scale 4
uv run pixel-forge export godot rune_chest --root examples
```
