# Bitmap Regions and the Vision Loop

**Goal:** Let real pixel data into the pipeline so art quality stops being capped by
`rect`/`ellipse`, and put eyes in the authoring loop so an agent can see what it drew.

**Why:** The shape DSL was a bootstrap renderer. Flat fills, no ramps, no outlines, and
axis-aligned primitives cannot produce readable pixel art at any effort level, and the
LLM authoring the coordinates never sees the result. Everything else in the toolkit
(anchors, mirroring, animation, validation, revisions, Godot export) is independent of
who draws the pixels, so the fix is to decouple the two.

**Non-goal:** Removing the shape DSL. It stays for shadows, blockouts, and simple
props, and a region may mix `bitmap` shapes with primitive shapes freely.

## Global Constraints

Everything in the existing plan still binds (Python 3.12+, determinism, no
antialiasing, mypy strict, ruff, line length 100). Additionally:

- A bitmap's pixels are **palette-indexed**, never raw RGB. The palette stays the
  single source of colour truth, so `PIX004` and the palette limit keep working.
- Import is **lossless or loud**: a source pixel that does not match a palette colour
  either fails with a report or is snapped explicitly at the caller's request. Never
  silently approximated.
- Determinism holds: importing the same PNG twice must produce a byte-identical spec.
  Character assignment must therefore be a pure function of the palette, not of dict
  iteration or discovery order.

---

## Contract 1: the `bitmap` shape op

```yaml
regions:
  body:
    anchor: torso
    layer: 20
    shapes:
      - op: bitmap
        at: [-10, -14]        # top-left, relative to the region's anchor
        key:                   # single char -> palette colour id
          o: ink
          m: suit_mid
          l: suit_light
          s: suit_shadow
        rows:
          - "...oooooo..."
          - "..ommmmmmo.."
          - ".ommllmmmmo."
          - ".ommmmmmmso."
```

Rules, all enforced at schema level:

- `.` and a space are **always** transparent and must not appear in `key`.
- Every non-transparent char in `rows` must appear in `key`; every `key` char must be
  a single character and must be used by at least one row.
- All rows must be the same length. A ragged bitmap is a schema error, not a pad.
- `key` values must be palette colour ids (checked at render, raising `PaletteError`).
- `BitmapShape` has **no `color` field** (it carries many), so it does not inherit
  `ShapeBase`. It joins the `Shape` union as its own discriminated member.

### Interaction with transforms

| Transform | Behaviour on a bitmap |
|---|---|
| `offset` | Applies to `at`, same as every other shape. |
| `visible` | Same as every other shape. |
| `color_swap` | Rewrites `key` **values**, so a swap of `suit_mid -> suit_dark` recolours the bitmap. |
| `scale_size` | **Ignored**, like `pixel` and `line`. Resampling art is not a size delta. Document this explicitly. |

---

## Contract 2: rendering

`rendering/shapes.py` keeps `draw_shape(canvas, shape, origin, rgba)` for the four
single-colour ops and gains:

```python
def draw_bitmap(canvas: Canvas, shape: BitmapShape, origin: Vec2,
                colors: Mapping[str, RGBA]) -> None
def bitmap_size(shape: BitmapShape) -> Vec2
```

`draw_shape` raises `RenderError` with an actionable message if handed a bitmap,
naming `draw_bitmap` as the correct call. `shape_bounds` handles bitmaps.

`rendering/compositor.py` branches on `op == "bitmap"`, resolving `key` through the
palette (after applying `color_swap`) into a `Mapping[str, RGBA]` before drawing.
Out-of-canvas pixels clip, exactly as they already do.

---

## Contract 3: PNG ingestion

New module `src/pixel_forge/rendering/ingest.py`, pure functions, no project or
filesystem knowledge beyond reading the image:

```python
@dataclass(frozen=True)
class IngestReport:
    width: int
    height: int
    matched: int                       # pixels matched exactly
    snapped: dict[str, int]            # "#rrggbb" -> count, snapped to nearest
    unmatched: dict[str, int]          # "#rrggbb" -> count, no match and snapping off
    added_colors: tuple[str, ...]      # palette ids created by --extend-palette
    trimmed_to: tuple[int, int, int, int] | None

def assign_chars(palette: ResolvedPalette) -> dict[str, str]
def png_to_bitmap(image, palette, *, snap=False, trim=True) -> tuple[BitmapShape, IngestReport]
def extract_palette(image, *, max_colors=24, palette_id="imported") -> Palette
```

- `assign_chars` is the determinism-critical piece: a pure function of the palette's
  declared colour order. Prefer the first unused alphanumeric character of the colour
  id, falling back to `a`, `b`, `c`, ... Same palette in, same map out, always.
- Alpha is binary: source alpha `>= 128` is opaque, below is transparent. Say so.
- `trim=True` crops to the opaque bounding box and reports the offset so `at` can be
  computed relative to the anchor.
- `extract_palette` builds a palette from an image's most frequent colours, so
  externally produced art (a generator, an artist, Aseprite) can be brought in without
  hand-transcribing hex codes. Deterministic ordering: by descending pixel count, ties
  broken by hex ascending.

---

## Contract 4: service layer, CLI, MCP

```python
# api.py
def import_region(root, asset_id, region, png_path, *, direction=None, at=None,
                  snap=False, extend_palette=False, replace=True,
                  timestamp, dry_run=False) -> ImportResult
def extract_palette_from_png(root, png_path, *, max_colors=24) -> Palette
def render_view(root, asset_id, *, animation, direction, frame=0, scale=8,
                out_path=None) -> ViewResult
def render_annotated_contact(root, asset_id, *, scale=4, out_path=None) -> ViewResult
```

- `import_region` records the change as a revision (`import_region` operation) with the
  previous shapes as its inverse, so bitmap art is as revertible as any other edit.
- Paths for the source PNG go through `safe_join` against the project root. Importing
  from outside the project is refused.

CLI:
```
pixel-forge import-region <asset> <region> --from <png> [--direction D] [--at X,Y]
                                           [--snap] [--extend-palette] [--dry-run]
pixel-forge extract-palette --from <png> [--max-colors N] [--json]
pixel-forge view <asset> --animation A --direction D [--frame N] [--scale 8] [-o PATH]
pixel-forge contact <asset> [--scale 4] [--annotate] [-o PATH]
```

MCP gains `import_region`, `extract_palette`, `render_view`, `render_annotated_contact`
with the same semantics.

---

## Contract 5: the vision loop

`render_annotated_contact` draws diagnostic overlays an agent can actually read:

- the declared `baseline_y` as a horizontal line
- every anchor as a 3x3 crosshair, labelled with the bitmap font
- the frame's silhouette bounding box
- a 1px grid at 8px intervals when `scale >= 4`

Overlays are drawn on a **copy**, never into the exported sheet, and use colours
outside the asset palette so they can never be mistaken for art.

`AGENTS.md` gains an "Art critique loop" section: render, view at scale, judge against
a written rubric (silhouette reads at 100%, consistent light direction, tonal ramp per
material, outline separates from background, forms connect, symmetry broken, anchors
line up with what they name), then edit and repeat. The rubric is the part that makes
the loop repeatable instead of vibes.

---

## Task breakdown

| Task | Files (disjoint) |
|---|---|
| 1. Schema + render | `schemas/common.py`, `rendering/shapes.py`, `rendering/compositor.py`, tests |
| 2. Ingestion | `rendering/ingest.py`, tests |
| 3. Annotation | `rendering/annotate.py`, tests |
| 4. Revisions + validation | `revisions/operations.py`, `validation/rules_pixel.py`, tests |
| 5. Wiring | `api.py`, `cli/*`, `mcp/server.py`, tests |
| 6. Proof | a re-authored character using bitmap art with ramps and outlines, docs |

Task 6 is the acceptance test for the whole effort: if the new sprite does not look
materially better than the current `engineer`, the work has not succeeded.
