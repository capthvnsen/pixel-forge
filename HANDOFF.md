# HANDOFF — Product Pivot: Single-Input Sprite Factory

_Last updated: 2026-08-06 — Alex + Hermes_

## TL;DR

Pixel Forge's product focus is now **one thing**: the user feeds the engine a
**single layered front-view drawing** of a character (separate layers for
arm/leg/torso/head — optional second input: one back view), and the engine
**programmatically produces all 8 directions plus the basic animation set**
(walk, run/jump, fall, idle, attack/swing-arm-with-weapon) and exports
Godot-ready assets. The user never touches coordinates, never hand-draws eight
views, never writes YAML by hand.

The existing engine is NOT thrown away. The YAML spec becomes the **intermediate
representation** the engine builds on your behalf, not the authoring surface.
Everything already built (deterministic renderer, polish, validation + repair
feedback, semantic ops, components, animation cycles, Godot export) becomes the
invisible backend that makes this simple front door produce professional output.

## Why this pivot (decision context)

- "Draw one sprite, get a full game character" is a creator tool with an
  instant, demoable wow factor. "Here's my YAML schema" is a developer tool.
- It is the logical end of the agent-first principle: the human works at the
  concept level (draw front, separate layers); the engine does the mechanical
  production. Nobody edits coordinates.
- It reuses ~90% of what already passed harsh adversarial review (below).

## The input contract (user supplies)

1. **Front view** (required) — single sprite drawing, with separate layers for:
   - `torso`
   - `head`
   - `arm_left`, `arm_right`
   - `leg_left`, `leg_right`
   - (optional: `weapon`/tool layer, `hair`/`hat` layer, `shadow`)
2. **Back view** (recommended, optional) — same layered split. Front+back lets
   the engine *interpolate* side views instead of hallucinating them.
3. Everything else (palette, canvas size, style) is either auto-derived or
   picked from the existing style packs / curated palettes.

## Output contract (engine produces)

- **8 directions**: N, NE, E, SE, S, SW, W, NW — mirrored where possible,
  projected/interpolated where not.
- **Animations**: walk (with gait/easing), jump, fall, idle (breathing), attack
  (swing-arm-with-weapon, joint-pivot at shoulder). Driven by the existing
  pose templates + walk-cycle generator, parameterized per direction.
- **Godot-ready**: `*.forge.json` manifests → the existing plugin imports them.
- Everything deterministic, palette-quantized, validated zero-blocking.

## What already exists (verified, do not rebuild)

| Capability | Where | Status |
|---|---|---|
| Deterministic rendering (spec → PNG, byte-identical) | `rendering/` | PASS through critic loops |
| Render polish (shading, AO, outline, ground shadow) | `rendering/effects.py` | P1 PASS 7/10 |
| Palette ramps + material hue discipline | `domain/palette.py` | P2 PASS 9/10 |
| Shape DSL (rect/ellipse/polygon/arc/curves/bitmap) | `rendering/shapes.py` | P3 PASS 9/10 |
| Walk cycles + easing + squash | `animation/cycles.py` | P4 PASS 8/10 |
| Terrain/tileset variation + seeding | `rendering/sheet.py`, `terrain.py` | P5 PASS 8/10 |
| Godot exporter fidelity | `exporters/godot/` | P6 PASS 9/10 |
| Quality scoring + repair feedback (QualityReport, 8 rules) | `validation/quality.py`, `rules_pixel.py`, `rules_animation.py` | W3-A PASS 9/10 |
| Semantic ops (swap_palette, apply_material, add_component, replace_component, change_pose, repair_outline) + protection + inverses | `revisions/operations.py` | W3-B closed (3 critic rounds) |
| Starter component library (backpack, helmet, shield, sword) | `src/pixel_forge/components/` | W3-B closed |
| Pose templates (idle, attack anticipation/strike) + role discovery (head/arm/leg/body) | `revisions/operations.py` | W3-B closed |
| Direction mirroring (`mirror: {dst: src}`) | schemas + rendering | exists |
| Curated palettes (rpg_fantasy etc.) | `references/curated.py` | exists |
| Progress page (live scores) | `.progress/` at localhost:8777 | exists |

Full gate as of this handoff: pytest ~922 green, mypy strict clean (68 files),
ruff clean, goldens 7/7, `build-all` 8/8 ok, 22 findings (all non-blocking).

## What must be built (the new work)

**Priority order — build and critic-criticize #1 FIRST; it is the hard part.**

1. **Direction projection from layered front (+back)** — the classic
   sprite-rotation problem, and the piece that will make or break the product.
   - front → back: mirror + strip face detail (doable).
   - front+back → side/diagonal: interpolate silhouettes and layer positions
     (NE/NW/SE/SW are the hardest; expect "consistent and game-ready", not
     "hand-drawn pixel master" — that is the right bar for the target user).
   - Side quality is what critics and buyers will judge hardest.
2. **Layered-art importer** — front PNG + layer mask(s) → a spec with layered
   regions (reuse `components/` + shape DSL; import path must respect the
   `export.polish: False` / external-source contract so imported art round-trips
   byte-exact until the user opts into polish).
3. **Joint-pivot limb articulation** — rotate a region around a joint anchor
   (shoulder for arms, hip for legs) in pure integer math; feeds swing-arm and
   walk/run legs. Bounded new primitive, not a research project.
4. **Per-direction animation parameterization** — existing walk/pose machinery
   already parameterizes by direction phase; wire it through the 8 produced
   views (side views get different limb phasing than front/back).
5. **Candidate review + feedback UI (Phase 6 of the earlier re-scope)** —
   silhouette view, palette map, frame-diff heatmap, onion-skin; this is what
   lets the user see the projected side views before committing.

## Known risks / honest caveats

- **Side/diagonal quality** is the make-or-break. Do #1 first, get it harshly
  criticized by a fresh-context adversarial critic against real references
  before building anything on top.
- **Don't let the importer violate determinism or the palette discipline** —
  imported art is byte-exact until polish is explicitly opted in; palette
  quantize on every derived pixel.
- **Keep the spec as source of truth** — the factory produces specs; every PNG
  and .tres stays a build artifact. No hand-editing generated output, ever.

## Working conventions (carry forward)

- Builder → fresh-context adversarial critic loop per piece; critics inspect
  only real on-disk artifacts (PNGs, code, tests), never builder summaries.
- Determinism gate: render twice → identical sha256. Goldens 7/7.
- Green gate: `uv run pytest`, `uv run mypy` (strict), `uv run ruff check .`,
  `pixel-forge build-all --root <copy> --force` → blocking False.
- Subagents: max 3 parallel, disjoint file sets, no destructive git ops in the
  shared worktree.
- Progress page + this HANDOFF stay updated as pieces land.

## Immediate next steps (when work resumes)

1. Write the scoped plan for piece #1 (direction projection) broken into
   builder-sized chunks, with the critic loop + acceptance bar designed up front
   (likely: silhouette overlap vs the reference back/side views, per-layer
   position deltas, determinism).
2. Dispatch builder(s) for #2 (importer) and #3 (joint pivot) in parallel with
   #1 if file sets are disjoint — they are (importer = api/schemas/import path,
   pivot = rendering/rotation helper, projection = rendering/direction).
3. After #1–#3 pass their critics: coherence pass over the whole
   draw-one-sprite → 8 directions → animated → Godot-imported flow, then the
   visual feedback tools (#4/#5), then the final honest scorecard.

## Context

- Repo: `/Users/alex/orca/projects/Pixelartllm-buddy` (all work uncommitted on
  `main`; nothing has been committed during the build-out — decide with Alex
  whether to commit the green baseline before starting the pivot work).
- Prior direction history: the earlier 8-point agent-first re-scope (quality
  scoring → semantic ops → component library → semantic DSL/style packs →
  seeded variation → visual feedback) remains the engine's foundation; the
  pivot is the product spine those pieces serve.
