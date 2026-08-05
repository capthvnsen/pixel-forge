# AI-Native Pixel Asset Forge for Godot — MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** A headless, deterministic, AI-native toolkit that turns structured YAML asset specs into validated pixel-art sprite sheets, atlases, previews, revision histories, and Godot 4.x import manifests, driven by a CLI and an MCP server.

**Architecture:** Pure domain core (`schemas` → `domain` → `rendering`/`animation`/`validation`/`preview`/`revisions`/`exporters`) with zero knowledge of CLI, MCP, or Godot. One shared service layer (`pixel_forge/api.py`) is the *only* thing the CLI and MCP server call, so both surfaces are guaranteed to agree. Pixels are never the source of truth: every asset is a YAML spec containing named regions built from a tiny deterministic shape DSL, positioned relative to named anchors, transformed per frame and per direction. Rendering is a pure function of the spec.

**Tech Stack:** Python 3.12+, uv, Pydantic v2, Typer, Pillow, NumPy, PyYAML, `mcp` (FastMCP), pytest, ruff, mypy, GDScript (Godot 4.4).

## Global Constraints

- Python `>=3.12`. Managed with `uv`. Package name `pixel-forge`, import name `pixel_forge`.
- Runtime dependencies limited to: `pydantic>=2.7`, `typer>=0.12`, `pillow>=10.3`, `numpy>=1.26`, `pyyaml>=6`, `mcp>=1.2`. No others without lead approval.
- **Determinism is a hard requirement.** No `datetime.now()`, `random`, `uuid4`, or set-iteration order inside render/export paths. Timestamps enter only through explicit parameters. Dict iteration must be insertion-ordered or explicitly sorted.
- **No antialiasing, ever.** All PIL resizing uses `Image.Resampling.NEAREST`. All coordinates are `int`. Alpha is strictly 0 or 255 in rendered output.
- All raster work happens on `numpy` arrays of shape `(h, w, 4)`, dtype `uint8`, RGBA, origin top-left.
- Every public function is fully type-annotated. `mypy --strict` must pass on `src/`.
- Line length 100. `ruff check` and `ruff format --check` must pass.
- Generated artifacts live under `build/` inside the asset project; editable sources live under `assets/`. Never write generated files into `assets/`.
- All filesystem access from CLI/MCP goes through `pixel_forge.domain.paths.safe_join`, which rejects escapes outside the project root.
- Schema documents carry `schema_version: 1`. Loaders must fail loudly on unknown versions.
- No GUI, no web frontend, no network calls, no auth, no database.

---

## File Structure

```
pyproject.toml                          # uv/hatchling project, deps, ruff, mypy, pytest config
AGENTS.md                               # agent-facing repository guide
README.md                               # human quick start
docs/adr/0001-architecture.md           # architecture decision record
docs/{schema,validation,godot,mcp}.md   # reference docs
schemas/*.schema.json                   # generated JSON Schema (build artifact, committed)

src/pixel_forge/
  __init__.py            __version__, SCHEMA_VERSION
  errors.py              ForgeError hierarchy
  api.py                 service layer — the ONLY entry point CLI + MCP use
  schemas/
    __init__.py          re-exports
    common.py            Vec2, Color, PaletteRef, ShapeOp union, Transform, Anchor
    palette.py           Palette, PaletteColor
    asset.py             AssetDoc discriminated union + CharacterAsset/EnemyAsset/PropAsset/TerrainAsset
    animation.py         AnimationSpec, FrameSpec, ProceduralAnimationSpec
    validation.py        Finding, Severity, ValidationReport
    manifest.py          AssetManifest, GodotManifest, SheetManifest
    revision.py          RevisionRecord, OperationSpec
    style.py             StyleProfile
    project.py           ProjectConfig
  domain/
    paths.py             safe_join, ProjectPaths
    project.py           Project (load/save/discover assets)
    palette.py           palette resolution, nearest-color, palette limits
    geometry.py          Rect, anchor math, bbox, silhouette
    hashing.py           stable content hashes (sha256 of canonical bytes)
    loader.py            YAML <-> pydantic with loud errors
  rendering/
    backend.py           RenderBackend Protocol
    canvas.py            Canvas — numpy RGBA raster primitives
    shapes.py            shape DSL -> pixel writes
    compositor.py        layer stack -> composed frame
    local.py             LocalRenderBackend — spec -> frames (the MVP backend)
    sheet.py             sprite sheet, atlas packing, contact sheet, seam maps
  animation/
    resolver.py          spec -> ResolvedFrame list (direction x animation x frame)
    timeline.py          transform interpolation for layered animation
  validation/
    engine.py            rule registry + runner
    rules_pixel.py       pixel-integrity rules
    rules_animation.py   animation-integrity rules
    rules_tileset.py     tileset-integrity rules
  preview/
    animated.py          GIF/WebP writers
  revisions/
    operations.py        operation registry + implementations + inverses
    store.py             revision log read/write, diffing
  references/
    profile.py           style profile create/update/scaffold reference dirs
  exporters/
    godot/
      manifest.py        neutral Godot import manifest builder
      spriteframes.py    SpriteFrames payload
      tileset.py         TileSet payload
      animation.py       AnimationPlayer payload
  cli/
    main.py              Typer app wiring
    commands.py          command implementations (thin over api.py)
  mcp/
    server.py            FastMCP server (thin over api.py)

godot/addons/pixel_asset_forge/   plugin.cfg, plugin.gd, importer.gd, dock.gd, validate.gd
godot/project.godot               sample Godot project
examples/{character,enemy,animated_prop,terrain}/
tests/{unit,integration,golden,end_to_end}/
```

---

## Interfaces (authoritative — every task must match these exactly)

```python
# schemas/common.py
Vec2 = tuple[int, int]

class ShapeBase(BaseModel):
    color: str                      # palette color id
    op: str

class PixelShape(ShapeBase):   op: Literal["pixel"];   at: Vec2
class LineShape(ShapeBase):    op: Literal["line"];    start: Vec2; end: Vec2
class RectShape(ShapeBase):    op: Literal["rect"];    at: Vec2; size: Vec2; fill: bool = True
class EllipseShape(ShapeBase): op: Literal["ellipse"]; at: Vec2; size: Vec2; fill: bool = True
Shape = Annotated[PixelShape | LineShape | RectShape | EllipseShape, Field(discriminator="op")]

class RegionTransform(BaseModel):
    offset: Vec2 = (0, 0)
    visible: bool | None = None
    color_swap: dict[str, str] = {}     # palette id -> palette id
    scale_size: Vec2 = (0, 0)           # additive px growth on rect/ellipse sizes

class Region(BaseModel):
    anchor: str
    layer: int
    shapes: list[Shape] = []
    mirror_safe: bool = True
    protected: bool = False

# schemas/animation.py
class FrameSpec(BaseModel):
    duration_ms: int
    events: list[str] = []
    transforms: dict[str, RegionTransform] = {}   # region name -> transform

class AnimationSpec(BaseModel):
    loop: bool = True
    frames: list[FrameSpec]
    procedural: ProceduralAnimationSpec | None = None

# schemas/asset.py
class AssetDoc(BaseModel):
    schema_version: Literal[1]
    asset: AssetHeader        # id, type, canvas, perspective, logical_pixel_scale, baseline_y
    palette: PaletteRef       # id + colors
    directions: list[str]
    mirror: dict[str, str] = {}          # dst direction -> src direction
    anchors: dict[str, Vec2]
    regions: dict[str, Region]
    direction_overrides: dict[str, dict[str, RegionTransform]] = {}
    animations: dict[str, AnimationSpec]
    export: ExportOptions
    validation: ValidationOptions

# rendering/canvas.py
class Canvas:
    def __init__(self, width: int, height: int) -> None
    @property
    def array(self) -> NDArray[np.uint8]          # (h, w, 4)
    def set_pixel(self, x: int, y: int, rgba: tuple[int,int,int,int]) -> None
    def draw_line(self, a: Vec2, b: Vec2, rgba) -> None      # Bresenham, integer only
    def draw_rect(self, at: Vec2, size: Vec2, rgba, fill: bool) -> None
    def draw_ellipse(self, at: Vec2, size: Vec2, rgba, fill: bool) -> None
    def blit(self, other: "Canvas", offset: Vec2) -> None    # source-over, binary alpha
    def mirror_x(self) -> "Canvas"
    def translate(self, offset: Vec2) -> "Canvas"
    def replace_color(self, src, dst) -> "Canvas"
    def colors(self) -> set[tuple[int,int,int,int]]
    def bbox(self) -> tuple[int,int,int,int] | None
    def to_image(self) -> PIL.Image.Image
    @classmethod
    def from_image(cls, img) -> "Canvas"

# animation/resolver.py
@dataclass(frozen=True)
class ResolvedFrame:
    direction: str
    animation: str
    index: int
    duration_ms: int
    events: tuple[str, ...]
    transforms: Mapping[str, RegionTransform]
    mirrored_from: str | None
def resolve_frames(doc: AssetDoc) -> list[ResolvedFrame]

# rendering/backend.py
class RenderBackend(Protocol):
    name: str
    def render_frame(self, doc: AssetDoc, frame: ResolvedFrame, palette: ResolvedPalette) -> Canvas: ...

# rendering/sheet.py
@dataclass(frozen=True)
class SheetCell: direction: str; animation: str; index: int; x: int; y: int; w: int; h: int
@dataclass(frozen=True)
class SpriteSheet: image: Canvas; cells: tuple[SheetCell, ...]; columns: int; rows: int
def build_sprite_sheet(frames, canvas_size, columns=None) -> SpriteSheet
def build_contact_sheet(sheet, labels) -> Canvas
def build_seam_map(tiles: Mapping[str, Canvas], layout) -> Canvas

# validation/engine.py
@dataclass(frozen=True)
class RuleContext: doc; frames: dict[tuple[str,str,int], Canvas]; palette; tiles
Rule = Callable[[RuleContext], list[Finding]]
def register(rule_id: str, *, kind: Literal["deterministic","heuristic"], applies_to: tuple[str, ...])
def run_validation(ctx: RuleContext) -> ValidationReport

# revisions/operations.py
def apply_operation(doc: AssetDoc, op: OperationSpec) -> tuple[AssetDoc, OperationSpec]   # -> (new doc, inverse op)

# api.py — every function returns a pydantic model; no printing, no sys.exit
def init_project(root, name) -> ProjectConfig
def new_asset(root, asset_type, asset_id, *, dry_run=False) -> AssetSummary
def list_assets(root) -> list[AssetSummary]
def get_asset(root, asset_id) -> AssetDoc
def render_asset(root, asset_id, *, dry_run=False) -> RenderResult
def validate_asset(root, asset_id) -> ValidationReport
def generate_preview(root, asset_id, *, fmt="gif") -> PreviewResult
def export_godot(root, asset_id) -> GodotManifest
def apply_asset_operation(root, asset_id, op, *, timestamp, dry_run=False) -> RevisionRecord
def compare_revisions(root, asset_id, rev_a, rev_b) -> RevisionDiff
def build_all(root) -> BuildReport
def test_seams(root, asset_id) -> SeamReport
```

---

## Tasks

### Task 0 — Scaffold (lead agent, done before dispatch)
`pyproject.toml`, package tree, all module stubs with the exact signatures above, tooling config, `.gitignore`, empty test dirs. Deliverable: `uv sync` works, `ruff check` passes, `pytest` collects zero tests without error.

### Task 1 — Schemas (`src/pixel_forge/schemas/**`, `tests/unit/test_schemas.py`)
Implement every pydantic model above plus `Palette`, `Finding`/`Severity`/`ValidationReport`, `RevisionRecord`/`OperationSpec`, `StyleProfile`, `ProjectConfig`, manifests. Discriminated unions on `op` (shapes) and `asset.type` (asset docs). Add `export_json_schemas(out_dir)`. Tests: valid docs parse; unknown `schema_version` raises; unknown shape `op` raises; palette id references validate.

### Task 2 — Canvas raster core (`rendering/canvas.py`, `rendering/shapes.py`, `tests/unit/test_canvas.py`)
Pure numpy. Bresenham lines, midpoint ellipse, source-over blit with binary alpha, mirror, translate, replace_color. `draw_shape(canvas, shape, origin, rgba)` dispatches the DSL. Tests assert exact pixel arrays for small canvases; assert no alpha value other than 0/255 is ever produced.

### Task 3 — Domain (`domain/**`, `tests/unit/test_domain.py`)
`safe_join` (reject `..`, absolute paths, symlink escapes), `ProjectPaths`, `Project.discover()`, YAML loader/dumper preserving key order, palette resolution + limit checks, geometry helpers (`silhouette_area`, `bbox`, `anchor_world_pos`), `content_hash(obj) -> str`. Tests include path-traversal attack cases.

### Task 4 — Animation resolver (`animation/**`, `tests/unit/test_animation.py`)
`resolve_frames` expands directions × animations × frames, applies `mirror` mapping (marking `mirrored_from`), merges `direction_overrides` under per-frame `transforms` (frame wins). `timeline.py` provides `lerp_transform` for layered transform animation keyframes. Tests: mirrored direction produces identical frame count; frame transform overrides direction override.

### Task 5 — Local render backend (`rendering/local.py`, `rendering/compositor.py`, `tests/unit/test_render.py`, `tests/golden/`)
Composite regions by `layer` ascending; region origin = `anchors[region.anchor] + transform.offset`; apply `color_swap`, `scale_size` (rect/ellipse only), `visible`. For a mirrored direction, render the source direction then `mirror_x`, then re-derive anchors by mirroring x about the canvas centre. Golden tests compare rendered PNG bytes to committed fixtures and write `*.actual.png` + a side-by-side diff PNG on failure.

### Task 6 — Sheets & atlases (`rendering/sheet.py`, `tests/unit/test_sheet.py`, golden)
Deterministic row-major packing grouped by (animation, direction). Contact sheet adds a 1px separator grid and a bitmap label strip (embed a tiny 3x5 built-in font — no external font files). Seam map tiles a 3x3 arrangement per tile and flags mismatching edge pixel runs.

### Task 7 — Preview (`preview/animated.py`, `tests/unit/test_preview.py`)
GIF (per-frame durations, transparency index, `disposal=2`) and WebP. Loop flag honoured. Test: written GIF reopens with the expected frame count and durations.

### Task 8 — Validation (`validation/**`, `tests/unit/test_validation_*.py`)
All rules from the spec, each with a stable `rule_id` (`PIX001`…, `ANI001`…, `TIL001`…), severity, and `kind` deterministic|heuristic. Findings carry asset/direction/animation/frame/region/measurements/remediation. Blocking = any `error`. Tests: one focused test per rule proving it fires and one proving it does not false-positive.

### Task 9 — Godot exporter (`exporters/godot/**`, `tests/unit/test_export_godot.py`)
Emit `build/godot/<asset_id>.forge.json`: schema version, asset id/type, texture paths relative to project root, `sprite_frames` (animation → frames with atlas rects + durations + loop), `pivots`, `baseline_y`, `events`, `tileset` (atlas source, tile coords, terrain sets, adjacency, collision/navigation/occlusion suggestions), `animation_player` tracks for prop transforms, `procedural` shader metadata, `import_settings` (nearest filter, no mipmaps). Golden-JSON tests.

### Task 10 — Revisions (`revisions/**`, `tests/unit/test_revisions.py`)
Operations: `resize_region`, `translate_region`, `recolor_region`, `set_frame_duration`, `add_frame`, `remove_frame`, `set_region_visibility`. Each returns its inverse. `apply_operation` refuses to touch regions with `protected: true` or anchors listed in `op.protect`. Store appends `RevisionRecord` to `assets/<id>/revisions.jsonl` with before/after hashes and validation summary. `compare_revisions` returns a machine-readable diff. Tests: apply→inverse→hash equals original; protected anchor violation raises.

### Task 11 — References & style profiles (`references/profile.py`, `tests/unit/test_references.py`)
`scaffold_references(root)` creates `references/{approved,inspiration,palettes,animation,rejected}` each with a README stating the no-tracing policy. `create_profile`/`update_profile` write `references/style_profile.yaml` with provenance entries (`source_path`, `role`, `notes`). Never overwrite files under `references/approved/`.

### Task 12 — API service layer (`api.py`, `tests/integration/test_api.py`)
Wire tasks 1–11 into the signatures above. Partial rebuild: skip re-render when the spec hash matches the recorded build hash unless `force=True`. `--dry-run` support means: compute and return the result without writing.

### Task 13 — CLI (`cli/**`, `tests/integration/test_cli.py`)
All commands from the spec. Global `--json` prints the result model as JSON to stdout. Exit codes: 0 ok, 1 validation blocking errors, 2 usage error, 3 internal error. Non-interactive always. Tests use Typer's `CliRunner`.

### Task 14 — MCP server (`mcp/server.py`, `tests/integration/test_mcp.py`)
FastMCP tools matching the spec's list, one per `api.py` function, strict in/out models, project root fixed at startup, every path validated through `safe_join`. No shell/eval tool. Tests call the tool functions directly.

### Task 15–18 — Four example assets + end-to-end tests
`examples/character/engineer.yaml` (4 directions, idle/walk/attack, 64×64), `examples/enemy/crawler.yaml` (idle/move/telegraph/attack/impact/death + events), `examples/animated_prop/beacon.yaml` (static base, rotating vane, pulsing light, layered transforms, one procedural shader entry), `examples/terrain/forest_tileset.yaml` (grass, dirt, transitions, animated water, adjacency, sample map). Each gets `tests/end_to_end/test_<name>.py` running load → render → validate → preview → export → assert artifacts exist → assert no blocking findings.

### Task 19 — Godot plugin (`godot/addons/pixel_asset_forge/**`, `godot/project.godot`)
Godot 4.4. `plugin.gd` registers a dock; `importer.gd` reads `*.forge.json`, validates it against the expected schema version, and builds `SpriteFrames`, `AnimationPlayer` `Animation` resources, and `TileSet` + `TileSetAtlasSource` via native APIs, saving to `res://generated/<asset_id>/`. Sets texture import to nearest/no-mipmaps. Never touches files outside `res://generated/`. Reimport updates in place by stable asset id. Ships `tools/godot_headless_import.sh` + `docs/godot.md` manual verification steps.

### Task 20 — Docs (`README.md`, `AGENTS.md`, `docs/**`)
Everything listed in the spec's AGENTS.md requirements, plus README quick start and honest known-limitations.

---

## Self-Review Notes

- **Deviation from writing-plans:** tasks specify exact interfaces and file ownership rather than literal full source for every step. Reproducing ~8k lines of implementation inside the plan would cost more than the implementation itself and would rot on first contact. The Interfaces section is the contract; each task owns disjoint files.
- **Spec coverage:** every numbered requirement in the product brief maps to a task above. Aesthetic checks land as heuristic warnings (Task 8). Procedural animation is schema + exporter contract with one beacon example (Task 18/19), as the spec allows.
- **Parallelism:** waves are {1,2} → {3,4,5,6,7} → {8,9,10,11} → {12} → {13,14} → {15..19} → {20}. No two concurrent agents share a file.
