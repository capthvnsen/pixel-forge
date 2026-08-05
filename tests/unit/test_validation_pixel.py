from __future__ import annotations

from typing import Any

from pixel_forge.animation.resolver import resolve_frames
from pixel_forge.domain.palette import resolve_palette
from pixel_forge.rendering.canvas import RGBA, Canvas
from pixel_forge.schemas import CharacterAsset, parse_asset_doc
from pixel_forge.validation import engine as engine_module
from pixel_forge.validation.engine import RuleContext, run_validation

RED: RGBA = (255, 0, 0, 255)
BLACK: RGBA = (0, 0, 0, 255)
BLUE: RGBA = (0, 0, 255, 255)
WHITE: RGBA = (255, 255, 255, 255)
OFF_PALETTE: RGBA = (17, 34, 51, 255)


def _doc(
    *,
    canvas: tuple[int, int] = (8, 8),
    baseline_y: int | None = None,
    logical_pixel_scale: int = 1,
    directions: list[str] | None = None,
    animations: dict[str, Any] | None = None,
    palette_colors: list[dict[str, Any]] | None = None,
    validation: dict[str, Any] | None = None,
) -> CharacterAsset:
    if directions is None:
        directions = ["south"]
    if animations is None:
        animations = {
            "idle": {"loop": True, "frames": [{"duration_ms": 100, "events": [], "transforms": {}}]}
        }
    if palette_colors is None:
        palette_colors = [{"id": "red", "hex": "#ff0000"}, {"id": "black", "hex": "#000000"}]
    data = {
        "schema_version": 1,
        "asset": {
            "id": "hero",
            "type": "character",
            "canvas": list(canvas),
            "baseline_y": baseline_y,
            "logical_pixel_scale": logical_pixel_scale,
        },
        "palette": {"id": "p", "colors": palette_colors},
        "directions": directions,
        "mirror": {},
        "anchors": {"root": [0, 0]},
        "regions": {"body": {"anchor": "root", "layer": 0, "shapes": []}},
        "direction_overrides": {},
        "animations": animations,
        "export": {},
        "validation": validation or {},
    }
    doc = parse_asset_doc(data)
    assert isinstance(doc, CharacterAsset)
    return doc


def _canvas(w: int, h: int, pixels: dict[tuple[int, int], RGBA] | None = None) -> Canvas:
    c = Canvas(w, h)
    for (x, y), rgba in (pixels or {}).items():
        c.set_pixel(x, y, rgba)
    return c


def _ctx(doc: CharacterAsset, frames: dict[tuple[str, str, int], Canvas]) -> RuleContext:
    return RuleContext(
        doc=doc,
        palette=resolve_palette(doc.palette),
        frames=frames,
        resolved=resolve_frames(doc),
        tiles={},
    )


# ---- PIX001: frame size must match doc.asset.canvas -----------------------------


def test_pix001_fires_on_size_mismatch() -> None:
    doc = _doc(canvas=(8, 8))
    ctx = _ctx(doc, {("idle", "south", 0): _canvas(4, 4)})
    report = run_validation(ctx, only=["PIX001"])
    assert len(report.findings) == 1
    assert report.findings[0].rule_id == "PIX001"
    assert report.findings[0].severity == "error"


def test_pix001_does_not_fire_on_matching_size() -> None:
    doc = _doc(canvas=(8, 8))
    ctx = _ctx(doc, {("idle", "south", 0): _canvas(8, 8)})
    report = run_validation(ctx, only=["PIX001"])
    assert report.findings == []


# ---- PIX002: alpha must be strictly 0 or 255 -------------------------------------


def test_pix002_fires_on_non_binary_alpha() -> None:
    doc = _doc()
    canvas = _canvas(8, 8)
    canvas.array[0, 0] = [10, 20, 30, 128]
    ctx = _ctx(doc, {("idle", "south", 0): canvas})
    report = run_validation(ctx, only=["PIX002"])
    assert len(report.findings) == 1
    assert report.findings[0].rule_id == "PIX002"
    assert report.findings[0].severity == "error"


def test_pix002_does_not_fire_on_binary_alpha() -> None:
    doc = _doc()
    canvas = _canvas(8, 8, {(0, 0): RED})
    ctx = _ctx(doc, {("idle", "south", 0): canvas})
    report = run_validation(ctx, only=["PIX002"])
    assert report.findings == []


# ---- PIX003: non-palette colour = AA artifact, skippable ------------------------


def test_pix003_fires_on_off_palette_colour() -> None:
    doc = _doc()
    canvas = _canvas(8, 8, {(0, 0): OFF_PALETTE})
    ctx = _ctx(doc, {("idle", "south", 0): canvas})
    report = run_validation(ctx, only=["PIX003"])
    assert len(report.findings) == 1
    assert report.findings[0].rule_id == "PIX003"
    assert report.findings[0].severity == "error"


def test_pix003_does_not_fire_when_allow_antialiasing() -> None:
    doc = _doc(validation={"allow_antialiasing": True})
    canvas = _canvas(8, 8, {(0, 0): OFF_PALETTE})
    ctx = _ctx(doc, {("idle", "south", 0): canvas})
    report = run_validation(ctx, only=["PIX003"])
    assert report.findings == []


# ---- PIX004: unapproved colour, never skippable ----------------------------------


def test_pix004_fires_on_unapproved_colour() -> None:
    doc = _doc()
    canvas = _canvas(8, 8, {(0, 0): OFF_PALETTE})
    ctx = _ctx(doc, {("idle", "south", 0): canvas})
    report = run_validation(ctx, only=["PIX004"])
    assert len(report.findings) == 1
    assert report.findings[0].rule_id == "PIX004"
    assert report.findings[0].measurements["color_hex"] == "#112233"


def test_pix004_does_not_fire_on_palette_only_colours() -> None:
    doc = _doc()
    canvas = _canvas(8, 8, {(0, 0): RED, (1, 1): BLACK})
    ctx = _ctx(doc, {("idle", "south", 0): canvas})
    report = run_validation(ctx, only=["PIX004"])
    assert report.findings == []


# ---- PIX005: palette size vs limit ------------------------------------------------


def test_pix005_fires_when_palette_exceeds_limit() -> None:
    doc = _doc(validation={"palette_limit": 1})
    ctx = _ctx(doc, {})
    report = run_validation(ctx, only=["PIX005"])
    assert len(report.findings) == 1
    assert report.findings[0].rule_id == "PIX005"
    assert report.findings[0].severity == "error"


def test_pix005_does_not_fire_when_within_limit() -> None:
    doc = _doc(validation={"palette_limit": 2})
    ctx = _ctx(doc, {})
    report = run_validation(ctx, only=["PIX005"])
    assert report.findings == []


# ---- PIX006: orphan pixels (heuristic) -------------------------------------------


def test_pix006_fires_on_isolated_pixel() -> None:
    doc = _doc()
    canvas = _canvas(8, 8, {(4, 4): RED})
    ctx = _ctx(doc, {("idle", "south", 0): canvas})
    report = run_validation(ctx, only=["PIX006"])
    assert len(report.findings) == 1
    assert report.findings[0].rule_id == "PIX006"
    assert report.findings[0].severity == "warning"
    assert report.findings[0].kind == "heuristic"


def test_pix006_does_not_fire_when_pixel_has_neighbour() -> None:
    doc = _doc()
    canvas = _canvas(8, 8, {(4, 4): RED, (5, 4): RED})
    ctx = _ctx(doc, {("idle", "south", 0): canvas})
    report = run_validation(ctx, only=["PIX006"])
    assert report.findings == []


# ---- PIX007: suspicious outline (heuristic) --------------------------------------


def _perimeter_coords(w: int, h: int) -> list[tuple[int, int]]:
    coords = [(x, 0) for x in range(w)] + [(x, h - 1) for x in range(w)]
    coords += [(0, y) for y in range(1, h - 1)] + [(w - 1, y) for y in range(1, h - 1)]
    return coords


def test_pix007_fires_on_small_stray_outline_colour() -> None:
    doc = _doc(
        canvas=(12, 12),
        palette_colors=[{"id": "black", "hex": "#000000"}, {"id": "white", "hex": "#ffffff"}],
    )
    canvas = Canvas(12, 12)
    canvas.draw_rect((0, 0), (12, 12), BLACK, fill=True)
    perimeter = _perimeter_coords(12, 12)
    canvas.set_pixel(*perimeter[0], WHITE)  # 1 of 44 perimeter pixels -> ~2.3%
    ctx = _ctx(doc, {("idle", "south", 0): canvas})
    report = run_validation(ctx, only=["PIX007"])
    assert len(report.findings) == 1
    assert report.findings[0].rule_id == "PIX007"
    assert report.findings[0].severity == "warning"


def test_pix007_does_not_fire_when_second_colour_is_common() -> None:
    doc = _doc(
        canvas=(12, 12),
        palette_colors=[{"id": "black", "hex": "#000000"}, {"id": "white", "hex": "#ffffff"}],
    )
    canvas = Canvas(12, 12)
    canvas.draw_rect((0, 0), (12, 12), BLACK, fill=True)
    perimeter = _perimeter_coords(12, 12)
    for x, y in perimeter[: len(perimeter) // 2]:
        canvas.set_pixel(x, y, WHITE)  # 50% of the edge -> not "small"
    ctx = _ctx(doc, {("idle", "south", 0): canvas})
    report = run_validation(ctx, only=["PIX007"])
    assert report.findings == []


# ---- PIX008: required frame must not be empty ------------------------------------


def test_pix008_fires_on_empty_frame() -> None:
    doc = _doc()
    ctx = _ctx(doc, {("idle", "south", 0): _canvas(8, 8)})
    report = run_validation(ctx, only=["PIX008"])
    assert len(report.findings) == 1
    assert report.findings[0].rule_id == "PIX008"
    assert report.findings[0].severity == "error"


def test_pix008_does_not_fire_when_frame_has_content() -> None:
    doc = _doc()
    ctx = _ctx(doc, {("idle", "south", 0): _canvas(8, 8, {(0, 0): RED})})
    report = run_validation(ctx, only=["PIX008"])
    assert report.findings == []


# ---- PIX009: fractional pixel scaling ---------------------------------------------


def test_pix009_fires_on_sub_scale_feature() -> None:
    doc = _doc(canvas=(4, 4), logical_pixel_scale=2)
    canvas = _canvas(4, 4, {(0, 0): RED})  # 1 of 4 pixels in the (0,0)-(1,1) block
    ctx = _ctx(doc, {("idle", "south", 0): canvas})
    report = run_validation(ctx, only=["PIX009"])
    assert len(report.findings) == 1
    assert report.findings[0].rule_id == "PIX009"
    assert report.findings[0].severity == "warning"


def test_pix009_does_not_fire_on_grid_aligned_feature() -> None:
    doc = _doc(canvas=(4, 4), logical_pixel_scale=2)
    canvas = _canvas(4, 4, {(0, 0): RED, (1, 0): RED, (0, 1): RED, (1, 1): RED})
    ctx = _ctx(doc, {("idle", "south", 0): canvas})
    report = run_validation(ctx, only=["PIX009"])
    assert report.findings == []


# ---- PIX010: inconsistent lighting metadata (heuristic) ---------------------------


def _lighting_doc() -> CharacterAsset:
    return _doc(
        canvas=(6, 6),
        palette_colors=[
            {"id": "red", "hex": "#ff0000"},
            {"id": "shadow", "hex": "#0000ff", "role": "shadow"},
        ],
        animations={
            "idle": {
                "loop": True,
                "frames": [
                    {"duration_ms": 100, "events": [], "transforms": {}},
                    {"duration_ms": 100, "events": [], "transforms": {}},
                ],
            }
        },
    )


def test_pix010_fires_on_inconsistent_shadow_direction() -> None:
    doc = _lighting_doc()
    frame0 = _canvas(6, 6, {(0, 0): RED, (5, 5): BLUE})  # shadow bottom-right -> SE
    frame1 = _canvas(6, 6, {(5, 5): RED, (0, 0): BLUE})  # shadow top-left -> NW
    ctx = _ctx(doc, {("idle", "south", 0): frame0, ("idle", "south", 1): frame1})
    report = run_validation(ctx, only=["PIX010"])
    assert len(report.findings) == 1
    assert report.findings[0].rule_id == "PIX010"
    assert report.findings[0].severity == "warning"


def test_pix010_does_not_fire_on_consistent_shadow_direction() -> None:
    doc = _lighting_doc()
    frame0 = _canvas(6, 6, {(0, 0): RED, (5, 5): BLUE})
    frame1 = _canvas(6, 6, {(0, 0): RED, (5, 5): BLUE})
    ctx = _ctx(doc, {("idle", "south", 0): frame0, ("idle", "south", 1): frame1})
    report = run_validation(ctx, only=["PIX010"])
    assert report.findings == []


# ---- engine mechanics --------------------------------------------------------------


def test_run_validation_only_filters_to_requested_rules() -> None:
    doc = _doc()
    ctx = _ctx(doc, {("idle", "south", 0): _canvas(4, 4)})  # wrong size -> PIX001
    report = run_validation(ctx, only=["PIX005"])
    assert report.findings == []


def test_run_validation_skip_excludes_requested_rules() -> None:
    doc = _doc()
    ctx = _ctx(doc, {("idle", "south", 0): _canvas(4, 4)})  # wrong size -> PIX001
    report = run_validation(ctx, skip=["PIX001"])
    assert all(f.rule_id != "PIX001" for f in report.findings)


def test_raising_rule_becomes_eng001_not_propagated() -> None:
    def _boom(_ctx: RuleContext) -> list[Any]:
        raise RuntimeError("kaboom")

    rule_id = "ZZZ001"
    meta = engine_module.RuleMeta(
        rule_id=rule_id,
        severity="error",
        kind="deterministic",
        applies_to=("character", "enemy", "prop"),
        description="test-only rule that always raises",
    )
    engine_module._REGISTRY[rule_id] = (meta, _boom)
    try:
        doc = _doc()
        ctx = _ctx(doc, {})
        report = run_validation(ctx, only=[rule_id])
    finally:
        del engine_module._REGISTRY[rule_id]

    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.rule_id == "ENG001"
    assert finding.severity == "error"
    assert finding.measurements["failing_rule"] == rule_id


def test_findings_are_sorted_by_rule_direction_animation_frame() -> None:
    doc = _doc(
        canvas=(8, 8),
        directions=["north", "south"],
        animations={
            "idle": {
                "loop": True,
                "frames": [{"duration_ms": 100, "events": [], "transforms": {}}],
            },
            "walk": {
                "loop": True,
                "frames": [{"duration_ms": 100, "events": [], "transforms": {}}],
            },
        },
    )
    frames = {
        ("idle", "north", 0): _canvas(4, 4),
        ("idle", "south", 0): _canvas(4, 4),
        ("walk", "north", 0): _canvas(4, 4),
        ("walk", "south", 0): _canvas(4, 4),
    }
    ctx = _ctx(doc, frames)
    report = run_validation(ctx, only=["PIX001"])
    order = [(f.direction, f.animation) for f in report.findings]
    assert order == [
        ("north", "idle"),
        ("north", "walk"),
        ("south", "idle"),
        ("south", "walk"),
    ]


def test_run_validation_filters_terrain_only_rule_out_for_sprite_asset() -> None:
    doc = _doc()
    ctx = _ctx(doc, {})
    report = run_validation(ctx, only=["TIL001"])
    assert report.findings == []


def test_blocking_true_iff_error_present() -> None:
    doc = _doc()
    error_ctx = _ctx(doc, {("idle", "south", 0): _canvas(4, 4)})  # PIX001 error
    error_report = run_validation(error_ctx, only=["PIX001"])
    assert error_report.blocking is True

    warning_ctx = _ctx(doc, {("idle", "south", 0): _canvas(8, 8, {(4, 4): RED})})  # PIX006 warning
    warning_report = run_validation(warning_ctx, only=["PIX006"])
    assert warning_report.blocking is False
