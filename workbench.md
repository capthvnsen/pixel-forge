# Workbench — Single-Input Sprite Factory (pivot build-out)

_Live progress file. Updated as pieces land. Authoritative requirements: HANDOFF.md._

## Baseline (verified 2026-08-07)

- Commit `dfb0761` on `main`, working tree clean.
- Green gate: **925 passed**, mypy strict clean (68 files), ruff clean.
- Goldens 7/7 (per HANDOFF).

## Resume (2026-08-07, deepseek-v4-flash picked up after kimi-k3 hit credit limit)

- Kimi left the tree RED: `import_layered` (api.py) called a never-written
  `_palette_from_composites` → 15 failing tests (importer + anchors).
- Resumed: implemented `_palette_from_composites` (deterministic hex-count
  palette extraction across composites, mirrors `ingest.extract_palette`),
  corrected a stale `baseline_y == 26` test expectation to `28` (union-bottom
  incl. ground shadow is what ANI001 actually measures — 26 is a blocking
  baseline drift), fixed 4 ruff E501s, formatted the new files.
- Green gate restored: **1007 passed**, mypy strict clean (69 files), ruff
  clean. Import determinism re-verified: render-twice byte-identical AND the
  engine-rendered idle/south/0 frame sha256 matches kimi's own
  `roundtrip_assertion.txt` (af608ce4…) — byte-exact round-trip vs source layers.

## Gauntlet round 2 (2026-08-07, after resume fixes + new pieces)

- **#1 eye defect FIXED**: the 3/4 squash's inverse mapping made the near eye's
  source column unreachable, so the far eye survived (cross-eyed tell on front
  diagonals). `_build_view` now mirrors a far-side-surviving face region across
  the centre axis. SE keeps the right eye (x=13), SW the left (x=10), left/right
  pairs exact mirrors. Regression test + vision-verified on PNGs.
- **#3 pivot invariant VERIFIED**: shoulder/hip anchor pixels stay opaque at
  joint colors through all 7 angles −90…+90. The +60° "detachment" the first
  critic saw is inherent thin-limb integer rotation, NOT a primitive bug;
  the fix belongs in parameterization (clamp angles), tracked in HANDOFF.
- **#4 BUILT**: `generate_joint_walk_cycle` (rotate-about-joint articulation,
  phase-periodic, `joint_walk` shader) + `project_animated_frames` (frames →
  8 projected views, occlusion reorder preserved, visible/color_swap). 16 new
  unit tests.
- **Coherence demo**: `.progress/pieces/coherence/make_demo.py` — layered PNGs
  → import → 8 rest dirs → joint walk → sheets. Deterministic, zero-blocking.
- **Round-2 critic verdict on the REAL product output (walk_sheet.png): NOT
  S-tier yet** — 3 concrete gaps: (1) back row still shows the face/eyes
  (importer embeds eyes in the head bitmap; projection only strips separately
  named face regions), (2) side rows stay front-facing, (3) extreme-stride leg
  crossing on thin limbs. Full detail in HANDOFF.md "Critic findings".

## Round 3 (2026-08-07, deepseek-v4-flash orchestrating)

- **Orchestrator pixel-verified round-2 critic gaps** (probe scripts in
  `.progress/pieces/coherence/probe_*.py`): (1) back view keeps eyes at
  (13,8),(17,8) in the head band; (2) east view keeps BOTH eyes at (14,8),(16,8)
  squeezed into a 3px span (symmetric squash can't make a profile); (3) walk
  X-blobs confirmed on real frames at ±35° on 3px limbs. South's eye pair
  (13,8),(17,8) and south_east's (14,8),(17,8) are CORRECT and must not change.
- **Two builders dispatched in parallel (disjoint files):**
  - Builder A (`rendering/direction.py` + `test_direction_projection.py`): strip
    interior-ink face features from the head region — ALL for back views
    (north/NE/NW), far-side-only for side views (east/west) so a true one-eye
    profile survives; diagonals unchanged; west stays an exact mirror of east.
  - Builder B (`animation/cycles.py` + new `test_walk_clamp.py`): geometry-aware
    swing clamp in `generate_joint_walk_cycle` — max_swing = min(requested,
    atan((hip_gap/2 - clearance)/leg_length)), floor 15°, so thin limbs stop
    crossing into X-blobs; thick limbs keep the full swing; missing-role fallback.
- **Critic reference harvested for the blind A/B**: composite LPC
  (Liberated Pixel Cup) character — body+head+pants+shoes+hair, 4 directions x 9
  walk frames, 64px cells, x4 (saved to /tmp/refs/lpc_reference_walk.png;
  builder script /tmp/refs/build_reference.py). Vision-verified complete and
  aligned. LPC is the community-standard 8-dir-class character walk reference
  and the honest comparison bar for the demo character's complexity class.

- **Orchestrator caught + fixed a cross-piece seam defect (after builders landed):
  mirrored views animated about UNMIRRORED pivots.** `_mirror_view` flipped
  each mirror-safe region's canvas, but `project_animated_frames` rotated every
  region about `doc.anchors[...]` — so west/SW/NW walk frames swung limbs
  around the wrong point: rest poses were exact mirrors, but walk frames
  were NOT (probe: west == mirror(east) failed on frames 0,1,3,4,5,7) and west
  legs merged into an X at max stride even after the swing clamp. Fix in
  `rendering/direction.py`: `ProjectedRegion` now records `anchor` (the
  per-view rotation pivot, mirrored for flipped content) and `mirrored` (flip
  parity); `_apply_frame_transform` negates the rotation angle + horizontal
  offset when `mirrored`; `_build_view(..., mirrored=back is None)` marks the
  back_base views. Verified on real pixels: west/SW/NW walk frames are now
  byte-exact mirrors of east/SE/NE; north == mirror(south) except the 2
  intentionally stripped eye pixels. Regression tests in the new
  `tests/unit/test_projected_walk_symmetry.py` (4 tests). Suite 1040 green,
  mypy strict clean, ruff clean, demo deterministic + zero-blocking.

## Round 3 (2026-08-07, deepseek-v4-flash orchestrating)

- **Round-3 fresh critic verdict (on the REAL demo output vs LPC reference): FAIL
  5/10.** All fixes verified on real pixels (back eyes gone, sides one eye,
  walk symmetry byte-exact, no X-blobs). BIGGEST_GAP: the face strip zeroed
  stripped pixels to alpha 0, leaving 1px transparent holes in the head
  silhouette of every back/side view — which RENDER AS EYE-SHAPED DOTS on a
  background (the blind A/B vision read the north row's holes as "two prominent
  eyes"). Secondary: (1) no near/far limb depth shading, (2) side/diagonal
  views are geometric squashes, (3) max-stride boots touch in N/S (clamp
  ignores boot width).
- **FIX LANDED (orchestrator): `_repaint_color` + strip repaints instead of
  transparent.** `rendering/direction.py`: `_strip_embedded_face` and
  `_strip_far_side_face` now repaint each stripped cluster with the modal
  opaque colour of its 4-neighbours (fallback: modal non-ink region colour).
  Hole probe (`.progress/pieces/coherence/probe_holes.py`) now reports ZERO
  holes in all 8 directions; regression test
  `test_stripped_face_leaves_no_holes_in_any_direction` added. Suite 1042
  green, mypy clean, ruff clean.
- **USER TEST (Alex's Aseprite upload) — pipeline works end-to-end:**
  `.progress/pieces/user_test/make_sheet.py` parses the legacy Aseprite format
  (no ASEF magic; old 0xA5E0 header + size-prefixed; chunk sizes INCLUDE the
  6-byte header; cels are zlib streams cut short before the final block —
  inflate with decompressobj, don't demand Z_STREAM_END). The file has 6 frames
  (6 colour variants) x 6 layers (Reference Layer 1 = 1122x1402 sketch;
  Layer 1 = merged head+torso; Layer 2/2(2) = arms; Layer 3/4 = feet).
  Pre-processing: split head+torso at row 18, extract the charcoal screen +
  cream eyes as a `face` layer (NEW optional import layer, added to
  `api.py` `_OPTIONAL_LAYERS` + z-order + anchor + docstring; the projection
  strips `face` regions from back views — the robot's rear view is now a clean
  beige panel, no screen, no holes; front/side/diagonals keep the screen).
  Outputs (x4): rest_8dirs, walk (8x8), idle (8x2), jump (8x6), arm_swing
  (8x5) — deterministic, validation blocking False.

## Round 4 (2026-08-07)

- **Round-4 fresh critic verdict: FAIL 6/10** (up from 5). The hole-repaint fix is
  VERIFIED at pixel level: ZERO transparent holes in all 8 rest views AND all 64
  walk frames; old hole positions now opaque (224,182,148,255) = exactly
  skin_mid; back views genuinely clean (vision: "0 dots/holes"); east exactly
  one near eye; suite 1042 green, determinism + blocking fine, mirror symmetry
  byte-exact.
- **BIGGEST_GAP (round 4): side views are geometric squashes, not profiles.**
  East head = 7px flat rectangle with centred eye + full front-layout shading;
  torso fat 10px block with arms fused inside; BOTH legs visible side-by-side
  with zero far-leg occlusion. 'Good' = occluded far limbs, near-side lighting,
  head silhouette with facial protrusion (the last is out of scope for a
  move-pixels-only engine — needs a SideView input or generative backend).
- **SECONDARY (round 4):** (1) boot contact at max stride frame 4 — N/S 8px
  contiguous run at y43; cycles.py clamp still needs boot-width geometry
  (round-3 ask half done); (2) no near/far limb depth shading.
- **Round-5 fixes dispatched (2 parallel builders, disjoint files):**
  - Builder A -> `rendering/direction.py`: NEW `occlude_far_limbs` ViewParam
    (True only for _SIDE) — true side views skip far-side limbs entirely, so
    east/west read as one-arm/one-leg profiles; diagonals/front keep both.
  - Builder B -> `animation/cycles.py`: boot-width-aware swing clamp
    (max_swing = atan((hip_gap/2 - boot_half_width - 1)/leg_length), floor
    lowered 15° -> 2° so geometry rules; boot row = leg region's bottom-row
    max contiguous run).

## Round 5 (2026-08-07)

- **Two fixes landed (parallel builders, disjoint files), orchestrator-verified:**
  - **Builder A -> `rendering/direction.py`:** NEW `occlude_far_limbs` ViewParam
    (True only for `_SIDE`) — `_build_view` skips far-side limbs in true side
    views. East/west now read as clean ONE-arm/ONE-leg profiles (vision
    confirmed on real sheets; builder probe: east arms=['arm_right']
    legs=['leg_right'], west mirrored, diagonals/front/back keep both; symmetry
    byte-exact rest + all 8 walk frames). Tests updated in
    test_projected_walk_symmetry.py (+3 new), test_direction_projection.py,
    test_directional_animation.py.
  - **Builder B -> `animation/cycles.py`:** boot-width-aware swing clamp —
    `_region_geometry` now measures the boot (bottom-row max contiguous run);
    `safe = atan((hip_gap - boot_width - 2*1px)/(2*leg_length))`; floor dropped
    15° -> 2° so geometry rules. Probe on the PROJECTED walk sheet: boot-row
    max run 4px in ALL 8 frames of south + north (was 8px fused at frame 4);
    demo swing now ~7.1°. test_walk_clamp.py updated (+2 new).
  - **Orchestrator:** updated `test_joint_walk_params_respected`
    (test_directional_animation.py) to the new clamp semantics (scout fixture
    caps at 4.0°; `max_swing: 90` override still yields 25°) — its old
    25°-regardless premise was exactly the critic's complaint.
- **Gates: 1047 passed, mypy strict clean, ruff check + format clean, demo
  deterministic + zero-blocking.**
- **Robot user-test sheets regenerated** with both fixes — side views now
  one arm + one foot (cleaner profiles); walk boots no longer fuse.

## Round 5.5 (2026-08-07)

- **Round-5 fresh critic verdict: FAIL 7/10** (up from 6). Occlusion +
  boot-clamp both pixel-verified airtight by the critic (east/west exactly one
  arm+leg with zero colour leak; boot rows 4px in ALL frames; mirrors
  byte-exact). The quality needle still didn't move vs the LPC reference.
- **BIGGEST_GAP (round 5): no near/far depth shading or volume** — the
  projection never uses the *_hi/_mid/_lo ramp steps (verified symptom: east
  profile keeps shirt_hi light stripe on the FAR side, zero shirt_lo
  anywhere). 'Good' = re-orient lighting (light near/chest, shade far/back)
  and shade far-side limbs one ramp step darker.
- **SECONDARY (round 5):** (1) mechanical gait — 8 frames use only 7°/5°/0°
  angle steps + 1px bob ("pogo stick"); arms barely swing because they inherit
  0.6x of the CLAMPED leg swing (~4°); (2) head silhouette has no facial
  protrusion (out of scope — move-pixels-only engine).
- **Round-6 fixes dispatched (2 parallel builders, disjoint files):**
  - Builder C -> `rendering/direction.py`: deterministic volume shading —
    infer ramp families from the HEX palette (hue-cluster + lightness-spread
    >= 40 guard so near-black ink/eye colours are excluded), shade far-side
    limbs one ramp step darker in diagonal views, flip light ramps hi<->lo on
    _SIDE views (light to the near/chest side). _FRONT (south) stays
    byte-identical; flat palettes (robot) byte-identical no-op.
  - Builder D -> `animation/cycles.py`: gait richness — arms counter-swing at
    0.6x of the REQUESTED joint_swing (~21°) instead of the clamped leg swing
    (~4°), preserve float angle precision (no int quantization), optional
    deterministic torso lean if it renders. Boot-fusion guarantee + loop-close
    + determinism must hold.

## Round 6 (2026-08-07)

- **Round-5 critic's biggest gap (no volume shading) + secondary (mechanical gait)
  both landed (parallel builders, disjoint files), orchestrator-verified:**
  - **Builder C -> `rendering/direction.py`:** hex-only ramp inference
    (`_infer_ramps` -> `_RampMap`: hue-run clustering, CIE L* >= 20, saturation
    >= 0.05, L* spread >= 8, 2-member spread <= 28, hue spread <= 8°, sat ratio
    <= 2.5 — ink/eye/near-blacks NEVER join a ramp). `shade_far_limbs` ViewParam
    (diagonals only): far limb one ramp step darker. `flip_light_side` (side
    views only): hi<->lo ramp flip so the light re-orients to the near/chest
    side. `_remap_colors` single-pass permutation remap. SOUTH/_FRONT stays
    byte-identical; flat palettes byte-identical no-op (robot sheets verified
    sha-identical to the pre-change baseline: walk c1c25112, rest 71ba9610,
    idle 460c1a9b, jump 3f3de29d, arm_swing d0c916ab).
  - **Builder D -> `animation/cycles.py`:** gait richness — arms now
    counter-swing at 0.6x the REQUESTED joint_swing (21° for the demo, was
    ~4° = 0.6x of the clamped leg swing), float-precision angle curves (7.125°/
    5.038°/0° instead of int-quantized 7/5/0; Canvas.rotate is fixed-point
    deterministic), upper body pumps as one mass. Boot rows still max 5px in
    ALL frames (no fusion). Suite 1049 green after Builder D.
  - **Orchestrator:** fixed Builder C's unfinished state — test
    `test_side_views_occlude_far_limbs` leg-colour assertions (3px fixture legs
    vanish under the 1/2 squash), added 6 new ramp tests in
    test_direction_projection.py (inference guards, side light re-orientation
    by SCREEN side, diagonal far-leg darkening by SCREEN side, flat no-op,
    ramp mirror invariance). KEY FINDING during verification: the far/near
    NAME sets do NOT map to screen sides in mirrored views (mirror flips the
    canvas) — the inherited-through-mirror shading (Builder C's original
    design) is CORRECT and mirror-consistent; a name-based "re-shading" fix
    would double-correct. Verified empirically per direction: dark leg on the
    camera-far side = SE left, SW right, NE right, NW left.
- **Gates: 1054 passed, mypy strict clean, ruff check + format clean, demo
  deterministic + zero-blocking.** Vision on real rest_sheet: light on the
  near/chest side in E/W profiles, far leg visibly darker in all 4 diagonals,
  S/N unchanged and clean.

## Round 7 (2026-08-07)

- **Round-6 fresh critic verdict: FAIL 6.5/10.** Gait fix fully verified (arms
  ±21.0° vs legs ±7.125°, float angles, zero boot fusion, loop continuous);
  wrong-side light gone (east shirt_hi count 0, shirt_lo only far side col 11);
  all gates green. BUT the diagonal leg depth shading was a SILENT NO-OP: the
  demo art never painted pants_lo, so import dropped it -> the pants ramp was
  2-step -> `darker(mid)=mid` -> far leg body identical to near. Secondary:
  (1) east/west chest light missing — the torso's 1px shirt_lo column was
  dropped by the 1/2 squash before the flip could turn it into shirt_hi;
  (2) belt (150,118,64) absorbed into the hair family (hair_lo dropped at
  import) -> east/west rendered the belt as gold hair_hi — a cross-material
  colour-identity violation; (3) side legs collapsed to 1-2px ink sticks
  (3px legs under the 1/2 squash).
- **ROOT CAUSE: demo art under-specified its ramps — the ENGINE shading was
  correct.** Orchestrator fixed the art in make_demo.py draw_layers:
  - legs 3px -> 4px (readable in side views), boots 4px, added a pants_lo
    boot-sole shadow (rows 47-48) so the FULL pants ramp survives import;
  - torso shirt_lo edge 1px -> 2px (cols 31-32) so the side squash keeps a
    column for the flip -> chest light;
  - belt recoloured to near-black brown belt_dark (52,38,30, L* < 20) so the
    ramp inference excludes it (can never be flipped into hair_hi);
  - hair_lo painted (right-edge shadow px) so the hair ramp is 3-step.
- **Verified (probe_r7.py):** all 4 ramp families now 3-step (shirt/pants/
  skin/hair); pants_lo in the imported palette; belt_dark excluded from ramps;
  east torso shirt_hi at x=19 (near) > shirt_lo x=11 (far); pants_lo pixels
  present in all 4 diagonals (SE/SW 20, NE/NW 10); east leg_right carries the
  full pants fill (not ink-only). Vision: far leg visibly darker along its
  whole length in all diagonals; chest light on the near side in profiles;
  belt dark everywhere; S/N clean. Gates: 1054 passed, mypy + ruff clean,
  demo deterministic + zero-blocking.

## Round 8 (2026-08-07)

- **Round-7 fresh critic verdict: FAIL 7.0/10.** Ramp fixes verified (3-step
  pants ramp, belt clean, chest light, readable legs, no fusion, mirror
  symmetry byte-exact). BIGGEST_GAP claimed SW/NW diagonal far/near leg
  shading INVERTED — **reviewed and determined to be a FALSE POSITIVE**
  (region-name trap: `_mirror_view` flips the canvas, so region names stop
  mapping to screen positions in mirrored views; SW == mirror(SE) is
  byte-exact, SE/NE confirmed correct by two critics, and a byte-exact mirror
  of a correct view is the correct opposite view; composite-level data shows
  the dark leg on the camera-far side in ALL four diagonals).
- **REAL secondary gap fixed: diagonal body-light re-orientation.**
  `flip_light_side` now applies to _DIAG_FRONT/_DIAG_BACK with a new
  `flip_limbs` flag (True only for _SIDE): the flip re-orients BODY regions
  (torso/head/hair) but EXCLUDES limbs so it never cancels the far-limb
  darkening. Verified: SE torso shirt_hi near/right + shirt_lo far/left; SW
  mirrored; far leg still one ramp step darker in all diagonals; vision reads
  coherent light direction across the whole sheet. New test
  test_ramp_diagonals_reorient_torso_light_to_near_side.
- **Gates: 1055 passed, mypy strict clean, ruff check + format clean, demo
  deterministic + zero-blocking.**

## Round 9 (2026-08-07)

- **Round-8 fresh critic verdict: FAIL 7.5/10** (score climbing; the round-7
  "SW/NW inverted" claim CONFIRMED false positive at composite level — all 4
  diagonals shade the camera-far half, mirrors byte-exact). Gates immaculate
  (1055, deterministic, zero holes, palette-pure south, boot rows <= 5px).
  Walk mechanics genuinely alternating (boot lift, arm phase).
- **BIGGEST_GAP (round 8): walk arm-swing amplitude** — hands travel ~8px on
  a 31px canvas and reach x=0/x=30 (canvas edges) at south frames 3-5,
  reading as flailing. 'Good' = geometry-clamped ~2-3px swing (hands stay
  inside the body column). Fix dispatched: Builder E -> cycles.py arm clamp
  (asin((hand_edge_distance - 1px)/arm_length), floor 2°, max_swing override
  skips it).
- **SECONDARY (round 8):** (1) sheet cell centering — source-canvas cells
  wasted ~35% dead space + direction-dependent offsets — FIXED by the
  orchestrator in make_demo.py (tight uniform panel from content bboxes +
  centered blits; rest_sheet 808->616px wide, walk 1608->1224, sprites
  centered, consistent offsets, no clipping); (2) E/W profile leg collapses
  to 1px at the passing pose; (3) sub-pixel bob + head at canvas top.

## Round 10 (2026-08-07)

- **Round-9 fixes landed + verified (orchestrator gates):**
  - **Builder E -> `animation/cycles.py`:** geometry-aware ARM swing clamp —
    `max_arm_swing = asin((hand_edge_distance - 1px)/arm_length)` per arm,
    min across both, floor 2°, `max_swing` override skips it. Probe on the
    PROJECTED south walk: arm max 4.096° (was 21°), hand x-range [2,29] in
    ALL 8 frames (was hitting x=0/x=30), boot rows still max 5px. Suite 1059
    green, mypy + ruff clean. Vision: hands stay inside, arms still visibly
    counter-swing, legs never fuse, walk reads natural.
  - **Orchestrator -> `make_demo.py`:** tight sheet packing (content-bbox
    panel + centered blits) — rest_sheet 808->616px wide, walk 1608->1224,
    consistent cell offsets, no clipping.

## Round 10 (2026-08-07)

- **Round-9 fresh critic verdict: PASS 7.5/10** — the first PASS across rounds
  3-9. Both round-9 fixes pixel-verified closed (arm clamp: hands x∈[2,29] all
  8 frames, anti-phase, loop delta 0, boots never fuse; sheet packing: tight
  36x56 panels, cell center axis x=19 / feet y=55 in ALL 72 cells -> grid
  slicing gives direction-independent anchors). Gates immaculate (1059,
  deterministic, zero holes, mirrors byte-exact, south palette-pure). Blind
  A/B still loses to the LPC reference on craft (blocky vs organic — the
  out-of-scope head-silhouette + art-craft frontier).
- **Art lever applied post-PASS (critic-sanctioned):** arms moved 1px inward
  (arm_left x=12, arm_right x=33) so the geometry arm clamp unlocks a visible
  swing — arm angles 8.213° (was 4.096°), hand travel ~2.5px/side (was 1px),
  still inside the canvas. Gates re-verified green.
- **MILESTONE COMMITTED + PUSHED:** commit f4bb6af
  ("feat: single-input sprite factory — 8-direction projection, joint walk,
  ramp shading (critic PASS 7.5/10)") — everything incl. workbench.md and the
  .progress/ demo + user-test pipeline; pushed dfb0761..f4bb6af to
  github.com/capthvnsen/pixel-forge (main).

## 4-Direction Sample Gauntlet (2026-08-07, NEW REFERENCE)

- **User direction change:** settle on 4 directions (south/west/east/north),
  match the quality of the Jephed sample pack (/Users/alex/Downloads/2D Top
  Down Pixel Art Characters.zip — 40 top-down character sheets, 20x32 cells,
  4 rows = down/left/right/up, 3-frame walk, chibi ~2.5 heads, 3-4 tone selout
  shading, dark 1px outlines). User feedback on the robot sheets: "torso is
  missing, side angles are a little squished".
- **Reference extracted:** /tmp/pf_refs4/s000_ref.png + s001/s005 (x4
  montages, sliced per the Reference.png grid: 20x32, 3 cols x 4 rows).
- **Test character authored (the gauntlet input):** chibi demo matching the
  samples' proportions — .progress/pieces/samples4/make_chibi.py (cyan hair,
  coral shirt, grey pants, 3-tone ramps, selout outlines, 8 layers: hair/
  head/face/torso/arm_left/arm_right/leg_left/leg_right — the custom-input
  contract). Imports at 16x32, zero-blocking.
- **4-direction builder:** .progress/pieces/samples4/make_ours.py — imports
  the chibi, projects south/west/east/north, 3-frame walk (frames 0/2/4 of
  the 8-cycle), packs rows in the samples' order (down/left/right/up).
  First output: clean front/profile/back per vision, torso proportionate.
- **Gauntlet loop:** round-1 fresh critic (mimo-v2.5-pro, vision) judging
  ours_walk_4dir.png vs s000_ref.png (blind A/B via /tmp/pf_tools/
  compare_ab4.py). On FAIL: one builder at the biggest gap, re-criticize.
- **Note on "torso missing":** the ROBOT's giant head (18/31 rows) leaves a
  tiny dark torso — an art-proportion issue; the chibi test character has
  balanced proportions so the gauntlet isolates engine quality.

## Plan (from HANDOFF.md priority order)

| # | Piece | Builder owns (files) | Status | Critic verdict |
|---|-------|----------------------|--------|----------------|
| 1 | Direction projection (front+back → 8 dirs) | `rendering/direction.py` (new), own new tests | **built, tests green, eye fix landed** | R2: back/side face strip is the open gap |
| 2 | Layered-art importer | `api.py`, `tests/integration/test_import_layered.py`, `tests/fixtures/layered/` | **built, tests green** | R2: byte-exact roundtrip confirmed |
| 3 | Joint-pivot limb articulation | `rendering/rotate.py`/`canvas.py`, `compositor.py`, `schemas/common.py`, `animation/resolver.py`, `animation/timeline.py`, own new tests | **built, tests green, invariant verified** | R2: wide-angle artifacts are parameterization, not primitive |
| 4 | Per-direction animation parameterization | `animation/cycles.py` (joint_walk), `direction.py` (project_animated_frames), own new tests | **built, tests green** | R2: pending (demo uses it) |
| 5 | Candidate review + feedback UI | TBD | pending | — |

## Acceptance bars (designed up front)

- **#1 projection**: determinism (render twice → byte-identical); palette discipline
  (projected pixels only use palette colors); left/right symmetry of projections;
  back view = mirrored front minus face detail (or matches supplied back view);
  side/diagonal silhouette and per-layer positions judged "consistent and
  game-ready" by a fresh-context critic inspecting actual PNGs.
- **#2 importer**: layered PNGs → spec; rendered front frame byte-exact vs source
  layers composited (polish off); anchors incl. joints synthesized; one revision
  logged; determinism; zero-blocking validation.
- **#3 pivot**: integer-exact rotate about joint anchor; determinism; pivot pixel
  invariant; merge/lerp/mirror support; swing-arm demo readable in PNGs.
- **#4 parameterization**: joint-pivot articulation (no slide), phase-periodic,
  deterministic, 8-direction coverage, side-occlusion preserved through frames.

## Critic evidence log

- **R2 (2026-08-07)**: direction contact sheet — eye fix confirmed (SE/SW face
  correctly, mirrors exact). Pivot sheet — primitive correct, wide-angle
  artifacts inherent. Coherence walk_sheet — NOT S-tier: back row shows face,
  side rows stay front-facing, thin-limb leg crossing at extreme strides.

## Remaining gaps

_(updated after each critic round)_
- Back/side face detail embedded in the head bitmap is not stripped/profiled
  (only separately named face regions are handled) — HANDOFF next step #1.
- Extreme-stride limb crossing on thin limbs — clamp in parameterization.
- Piece #5 (candidate review + feedback UI) not started.
- Nothing since `dfb0761` is committed yet.
