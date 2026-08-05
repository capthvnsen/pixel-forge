# vanguard (character, bitmap regions)

A 32x32 four-direction trooper drawn with `op: bitmap` shapes instead of primitives.

<img src="../../previews/primitives_vs_bitmap.png" width="700" alt="primitives versus bitmap regions">

Left: `engineer`, built from `rect` and `ellipse`. Right: `vanguard`, built from
bitmap regions. Same canvas pipeline, same anchors, same mirroring, same Godot export.

## Why this asset exists

It is the acceptance test for bitmap regions. The shape DSL cannot express an outline,
a tonal ramp, or a non-rectangular form, so every sprite built from it reads as flat
blocks no matter how much effort goes into the coordinates. This asset proves the
pipeline's value was never in the drawing: swap the pixel source and everything else
keeps working unchanged.

## What it demonstrates

- **Outlines.** Every region carries its own `ink` border, so forms separate from the
  background and from each other.
- **Tonal ramps.** `armor_lite`/`armor_mid`/`armor_dark` share `ramp: armor`; the same
  for `metal` and `visor`. Light is consistently upper-left: highlights on the left
  shoulder and left leg, shadow down the right side.
- **Declared ramp metadata.** Colours carry `ramp` and `role`, which is what lets
  `PIX012` distinguish deliberately flat art from art someone forgot to shade.
- **Mixed shapes in one asset.** The ground shadow is still an `ellipse` primitive,
  because a flat ellipse is genuinely the right tool for it. Bitmaps did not replace
  the DSL, they joined it.
- **Bitmaps under every existing feature.** `east` is mirrored from `west`. `north`
  uses `color_swap` on a bitmap's `key` to hide the visor glow and the chest accent
  when the character faces away. Both work without bitmap-specific handling.

## Authoring notes

Three passes through `pixel-forge view` produced this. Each pass fixed something that
was invisible in the YAML:

1. A 2px transparent gap at the hips, because the `legs` anchor sat below the torso's
   last row. Moving the anchor from y=22 to y=20 closed it.
2. Arms starting at mid-torso instead of the shoulder, fixed by moving both arm
   anchors from y=14 to y=12.
3. A gun floating off the hand, fixed by moving `right_hand` from x=24 to x=22.

Validation then caught 40 blocking `ANI004` errors. Frames written as
`transforms: {}` do not name the regions whose resolved position changed relative to
the previous frame, so the rule reads it as anchor drift. The fix is to write the
identity explicitly:

```yaml
- { duration_ms: 200, transforms: { torso: { offset: [0, 0] }, head: { offset: [0, 0] } } }
```

This is a genuine sharp edge in the current animation model, and it is worth knowing
before you author your own: an empty `transforms` map is not the same as "everything
at rest".

## Importing art instead of authoring it

The same region can be filled from any PNG:

```sh
uv run pixel-forge extract-palette --from art/trooper.png --max-colors 16
uv run pixel-forge import-region vanguard torso --from art/torso.png --extend-palette
```

Import is palette-indexed, so `PIX004` and the palette limit keep working on imported
art. Unmatched colours are reported by hex and count rather than silently dropped, and
an import that would lose more than half the source pixels fails loudly instead of
succeeding with a hole in it.

Note the current limitation: `regions` are shared across directions, so there is no
way to import genuinely different art per direction. `import_region` raises rather
than pretending otherwise.

## Validation

0 errors, 96 warnings, not blocking.

| Rule | Count | Why it is acceptable |
|---|---|---|
| `PIX007` | 96 | Heuristic: flags a colour covering a small share of the silhouette edge. On an outlined sprite the highlight colours on the left shoulder and leg touch the edge by design, which is what a lit edge looks like. |

## Build it

```sh
uv run pixel-forge render vanguard --root examples
uv run pixel-forge validate vanguard --root examples
uv run pixel-forge view vanguard --animation idle --direction south --scale 8 --root examples
uv run pixel-forge build vanguard --root examples
```
