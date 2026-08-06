# Validation rules

Every rule is a plain function registered with `@register(...)` in
`src/pixel_forge/validation/rules_pixel.py`, `rules_animation.py`, or
`rules_tileset.py`, and run by `run_validation` (`validation/engine.py`) in sorted
`rule_id` order. `pixel-forge validate <id>` and `validate_asset`/`get_validation_report`
(MCP) run every rule whose `applies_to` matches the asset's type; there is no CLI or
API surface today to run a subset (`run_validation` accepts `only`/`skip` sequences,
but nothing in `api.py` exposes them to a caller — not verified to be reachable outside
tests).

`kind: deterministic` means the same doc always produces the same findings for that
rule. `kind: heuristic` means the rule is a judgment call tuned against the four
shipped examples, not a guarantee — expect false positives/negatives on real art with
different silhouette conventions (see the "Known limitations" note in `README.md`).

A rule that raises an unhandled exception is caught by `run_validation` and turned
into an `ENG001` error finding naming the failing rule, rather than crashing the whole
validation pass.

## Pixel-integrity rules (`PIX0xx`) — sprites only (`character`, `enemy`, `prop`)

| Rule | Severity | Kind | Checks | How to fix / relax |
|---|---|---|---|---|
| `PIX001` | error | deterministic | Every rendered frame's `(width, height)` equals `doc.asset.canvas`. | Check region offsets/`scale_size` aren't pushing content outside the canvas, or update `asset.canvas`. |
| `PIX002` | error | deterministic | Alpha is strictly `0` or `255` on every pixel (binary transparency; the renderer never produces partial alpha, so this catches a broken/foreign backend). | Re-render with the local backend; never write partial-alpha pixels. |
| `PIX003` | error | deterministic | A non-palette colour lies on (or within rounding of) the segment between two palette colours in RGB space — the exact signature an antialiasing/blend artifact leaves. | Re-render nearest-neighbour only, or set `validation.allow_antialiasing: true` if intentional. |
| `PIX004` | error | deterministic | A non-palette colour that is *not* a blend of two palette colours (that's `PIX003`'s concern) — a genuinely unapproved colour, never waivable via `allow_antialiasing`. | Use only palette colours, or add the colour to the palette. |
| `PIX005` | error | deterministic | Palette colour count `<= validation.palette_limit` (default 24). | Trim the palette or raise `validation.palette_limit`. |
| `PIX006` | warning | heuristic | Orphan pixels: an opaque pixel with zero opaque 8-neighbours. | Remove the stray pixel or connect it to the surrounding shape. |
| `PIX007` | warning | heuristic | Silhouette-outline consistency: silhouette-edge pixels (opaque, 4-adjacent to transparent/canvas edge) are grouped by colour; any non-dominant colour covering under 10% of the edge is flagged as a likely stray pixel or accidental palette swap. | Check for a stray outline pixel or an accidental colour swap; a legitimately small accent colour near the edge can also be moved further inside the shape (see `engineer.yaml`'s accent-stripe inset convention). |
| `PIX008` | error | deterministic | A frame required by an animation is not entirely empty (zero opaque pixels). | Check region visibility/offset for that frame; something should render. |
| `PIX009` | warning | deterministic | When `asset.logical_pixel_scale != 1`, every opaque feature is aligned to that scale's grid (checked via a downscale-by-scale/upscale-by-scale round trip). | Align every shape to the declared logical pixel grid. |
| `PIX010` | info / warning | heuristic | Lighting-direction consistency: if any palette colour declares `role` containing `"shadow"`/`"light"` or a non-null `ramp`, each frame's shadow-pixel centroid (relative to bbox centre) is bucketed into a compass direction; disagreement across frames warns. With no lighting metadata at all, emits a single `info` finding instead of silently passing. | Tag shadow/light palette colours with `role`/`ramp` to enable the check; keep the shadow side consistent across frames for one light source. |

## Animation-integrity rules (`ANI0xx`) — sprites only

| Rule | Severity | Kind | Checks | How to fix / relax |
|---|---|---|---|---|
| `ANI001` | error | deterministic | Baseline drift: measured lowest opaque row equals `doc.asset.baseline_y`, for every rendered frame. Skipped entirely if `baseline_y` is `None` or `validation.require_stable_baseline` is `false`. | Adjust region offsets so the lowest opaque row matches `baseline_y`; or set `require_stable_baseline: false`. |
| `ANI002` | error | deterministic | Foot-anchor drift: the `feet` anchor (or, absent that, the anchor of a region named `*shadow*`/`*body*`; if neither exists, the rule is a no-op) must resolve to one world position across every frame of an animation+direction. Gated by `validation.require_stable_anchors`. | Keep the foot/body anchor's offset constant across an animation's frames; or set `require_stable_anchors: false`. |
| `ANI003` | error (looping) / warning (non-looping) | deterministic | Pivot drift: frame bbox centre-x must not move more than 2px between consecutive frames. | Keep the silhouette horizontally centred; large jumps read as popping. |
| `ANI004` | error | deterministic | Attachment-anchor drift: any anchor other than `feet` must not move between frames unless that frame's own `transforms` dict explicitly sets an offset for a region using it. | Add an explicit `transforms` entry for the region in that frame, or remove the unintended drift. |
| `ANI005` | warning | heuristic | Loop popping: for a looping animation, the last frame's opaque mask differs from the first frame's by more than 35% (XOR over the larger of the two opaque counts). | Make the last frame closer to the first, or add a blend frame. |
| `ANI006` | warning | heuristic | Palette flicker: a colour present in frame N and N+2 but absent from N+1. | Check for a missing colour swap or a dropped shape mid-cycle; an *intentional* blink (e.g. a beacon lamp) is expected to trip this — see `examples/assets/beacon/README.md`. |
| `ANI007` | error | deterministic | Every declared animation has at least one frame, and every resolved frame has a matching rendered canvas. | Add at least one frame to an empty animation; ensure the renderer produced output for every resolved frame. |
| `ANI008` | warning | heuristic | Silhouette-volume stability: opaque pixel count must not change by more than 40% between consecutive frames (relative to the previous frame's count). | Check for a dropped or duplicated region between frames. |
| `ANI009` | error | deterministic | Directional consistency: every direction of one animation must agree on frame count and per-frame durations. | Use the same frame count and durations across every direction of an animation. |

## Tileset-integrity rules (`TIL0xx`) — terrain only

| Rule | Severity | Kind | Checks | How to fix / relax |
|---|---|---|---|---|
| `TIL001` | error | deterministic | Every `transitions[].tile_id` exists in `tiles`, and every terrain pair referenced by any tile/transition has at least one transition tile. | Declare the missing tile, or add a `TransitionSpec` connecting the pair. |
| `TIL002` | error | deterministic | Every tile id in a `TerrainSet.tiles` list exists in `tiles`. | Declare the tile, or remove it from the set. |
| `TIL003` | error (self-pair) / warning (cross-pair) | deterministic | Seam check via `check_seams`: for every tile pair and edge, mismatched edge pixels above `validation.max_seam_mismatch` (default 0). A tile's mismatch against *itself* is an error (it won't tile against its own repetitions); a mismatch against a *different* tile is a warning. | Adjust the tile's edge pixels so it tiles seamlessly against its neighbour; or raise `max_seam_mismatch`. |
| `TIL004` | error | deterministic | Animated-tile integrity: every frame of an `animated_tiles` entry must share one size, and each referenced frame tile must tile against *itself* within `max_seam_mismatch`. | Render every frame of an animated tile at the same size; fix each frame's self-seam. |
| `TIL005` | error | deterministic | `sample_map` shape/content integrity: every layer's row count matches `sample_map.size`'s height, every row's length matches its width, and every cell names a known tile id. | Pad/trim the layer to match `sample_map.size`; fix unknown tile references. |
| `TIL006` | warning | deterministic | Tiles within one `TerrainSet` should all declare the same `collision` value. | Make every tile in a terrain set agree on its `collision` metadata. |
| `TIL007` | warning | heuristic | Excessive repetition: in a `sample_map` layer, the single most frequent tile's share of all cells exceeds `validation.max_repeat_ratio` (default 0.6). | Vary tile placement; a single dominant tile reads as repetitive. Or raise `max_repeat_ratio`. |

## External-source rules (`SRC0xx`) — sprites with a `source:` block

These only apply to assets whose pixels come from PNGs on disk via
`ExternalFrameBackend`. They exist because that backend ignores parts of the spec the
shape-DSL renderer honours, and a spec edit that silently does nothing is worse than
one that fails.

| Rule | Severity | Kind | Checks | How to fix / relax |
|---|---|---|---|---|
| `SRC001` | warning | deterministic | A `source:` asset declares non-empty `regions`, `direction_overrides`, or per-frame `transforms`. The external backend reads none of them, so the declaration has no effect on the rendered output. | Remove the declaration, or drop `source:` and render the asset from the shape DSL. Composite and transform work has to happen in the art itself when the pixels come from files. |
| `SRC002` | warning | deterministic | A direction listed in `mirror` also has its own frame file present on disk. The mirror table wins and that file is never read. | Either remove the direction from `mirror` so its own art is used, or delete the unused file. |

## Engine-level finding

| Rule | Severity | Kind | Meaning |
|---|---|---|---|
| `ENG001` | error | deterministic | A validation rule itself raised an exception while running. Not a rule about the asset — a rule about the toolkit. Names the failing `rule_id` in `measurements.failing_rule`. |

## Relaxing rules via `validation:`

There is no per-rule enable/disable switch in the spec. The knobs that exist are the
six `ValidationOptions` fields (`docs/schema.md`), each gating one or a small group of
rules:

```yaml
validation:
  palette_limit: 32          # PIX005
  require_stable_baseline: false   # skips ANI001 entirely
  require_stable_anchors: false    # skips ANI002 entirely
  allow_antialiasing: true         # skips PIX003; PIX004 still runs, but never fires on a blend
  max_seam_mismatch: 1             # TIL003 / TIL004 tolerance, in pixels
  max_repeat_ratio: 0.8            # TIL007 threshold
```

Every other rule always runs for every asset of a matching type; there is no
project-wide or per-asset way to silence e.g. `PIX007` or `ANI005` short of accepting
their findings (they're `warning`/`info`, so they never block a build) or fixing the
underlying art.
