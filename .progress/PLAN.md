# Pixel Forge — Perfection Loop Plan (rev. 2, incorporating Alex's agent-first feedback)

## Quality bar (unchanged)
CraftPix/IsoCore/PixelLab-level top-down pixel art, game-ready for Godot, verified by
fresh-context adversarial critics against REAL reference sheets (blind A/B + pixel forensics).

## Completed (verified by orchestrator + critics)
- WAVE 1 (art core): P1 render polish PASS 7/10 (+ per-region form shading), P2 palette discipline PASS 9/10, P3 shape DSL PASS 9/10.
- COHERENCE 1: per-region form shading (composite_tagged); docs/schema.md updated; all 8 forge.json manifests import into real Godot 4.7.1 (0 failed).
- WAVE 2 (game-readiness): P4 animation PASS 8/10 (+ passing-pop micro-fix), P6 Godot exporter PASS 9/10 (durations verified in real .tres).
- P5 terrain: R1 4/10 → R2 6/10 → R3 7/10 → R4 fix landed (content-repetition lattice: full re-scatter + sha256 variant hash + interior ramp tones + real water transitions), R4 critic in flight.

## Wave 3 — agent-first layer (Alex's feedback, his recommended order)
The engine's renderer is strong. The winning improvement: let agents operate through
art concepts, reusable components, constraints, and feedback — not coordinate-level pixels.

- W3-A (Alex Phase 1): **Machine-readable quality scoring + repair feedback** — upgrade validation to emit {score, issues:[{type, region, coordinates, suggested_fix}]}; add quality rules: orphan pixels, noisy clusters, banding, jagged curves, inconsistent outlines, weak silhouette, animation jitter, shifting body volume. → this replaces old P7.
- W3-B (Alex Phase 2): **Agent editing operations** — semantic, safe ops on top of revisions: add_component, replace_component, move_anchor, change_pose, swap_palette, apply_material, increase_silhouette_width, repair_outline, simplify_clusters, generate_variants. → replaces old P7 remainder.
- W3-C (Alex Phase 3): **Curated component + pixel-cluster library** — helmets, heads/faces, torsos, arms/legs, weapons, backpacks, foliage, rocks, buildings, pixel-cluster patterns; components are YAML spec fragments agents assemble. → replaces old P8.
- W3-D (Alex Phase 4): **Semantic DSL + style packs** — archetype/style/proportions/perspective/body-part/material/lighting front-end compiled to anchors/regions/shapes/palettes; style packs (pokemon_gba_overworld etc.) as locked rule sets (canvas, proportions, palette limit, outline rules, light direction, cluster sizes, dithering, animation cadence, perspective, contrast).
- W3-E (Alex Phase 5): **Seeded variation + candidate sheets** — variation:{seed, wear, asymmetry, texture_density} → reproducible scratches/decals/terrain/faces/equipment; candidates:{count, vary:[...]} → render one contact sheet, score every candidate, agent refines best 2-3.
- W3-F (Alex Phase 6): **Visual feedback tools** — enlarged NN preview, silhouette-only, palette map, region/anchor overlay, animation strip, onion-skin, frame-difference heatmap, before/after diff.

## Wave 3 flow
Builders (parallel per wave, disjoint files) → fresh-context critics judging REAL output
+ candidates → fix loops until PASS → coherence pass 2 → final full verification
(pytest/mypy/ruff/determinism/golden/Godot) → final honest scorecard vs the three references.

## Cross-cutting rules (unchanged)
- Determinism: pure integer math in polish; seeded variation is deterministic-by-seed.
- Spec-as-source-of-truth; goldens regenerated centrally + hand-reviewed.
- Builders never run destructive git ops; disjoint file ownership; critics judge artifacts, never summaries.
