from __future__ import annotations

from typing import Any

from pixel_forge.animation.resolver import resolve_frames
from pixel_forge.domain.palette import expand_palette, resolve_palette
from pixel_forge.rendering.canvas import RGBA, Canvas
from pixel_forge.schemas import CharacterAsset, parse_asset_doc
from pixel_forge.validation import engine as engine_module
from pixel_forge.validation.engine import RuleContext, run_validation

RED: RGBA = (255, 0, 0, 255)
BLACK: RGBA = (0, 0, 0, 255)
BLUE: RGBA = (0, 0, 255, 255)
WHITE: RGBA = (255, 255, 255, 255)
OFF_PALETTE: RGBA = (17, 34, 51, 255)
# Exactly on the RGB segment between the default (red, black) palette: a genuine
# antialiasing blend, unlike OFF_PALETTE above which lies on no palette segment.
BLEND_RED_BLACK: RGBA = (128, 0, 0, 255)


def _doc(
    *,
    canvas: tuple[int, int] = (8, 8),
    baseline_y: int | None = None,
    logical_pixel_scale: int = 1,
    directions: list[str] | None = None,
    animations: dict[str, Any] | None = None,
    palette_colors: list[dict[str, Any]] | None = None,
    validation: dict[str, Any] | None = None,
    regions: dict[str, Any] | None = None,
) -> CharacterAsset:
    if directions is None:
        directions = ["south"]
    if animations is None:
        animations = {
            "idle": {"loop": True, "frames": [{"duration_ms": 100, "events": [], "transforms": {}}]}
        }
    if palette_colors is None:
        palette_colors = [{"id": "red", "hex": "#ff0000"}, {"id": "black", "hex": "#000000"}]
    if regions is None:
        regions = {"body": {"anchor": "root", "layer": 0, "shapes": []}}
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
        "regions": regions,
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


def _ctx(
    doc: CharacterAsset,
    frames: dict[tuple[str, str, int], Canvas],
    *,
    polish_shadow_rows: int = 0,
    palette_override: Any | None = None,
) -> RuleContext:
    return RuleContext(
        doc=doc,
        palette=palette_override if palette_override is not None else resolve_palette(doc.palette),
        frames=frames,
        resolved=resolve_frames(doc),
        tiles={},
        polish_shadow_rows=polish_shadow_rows,
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


# ---- PIX003: palette-blend = AA artifact, skippable ------------------------------


def test_pix003_fires_on_blend_colour() -> None:
    doc = _doc()
    canvas = _canvas(8, 8, {(0, 0): BLEND_RED_BLACK})
    ctx = _ctx(doc, {("idle", "south", 0): canvas})
    report = run_validation(ctx, only=["PIX003"])
    assert len(report.findings) == 1
    assert report.findings[0].rule_id == "PIX003"
    assert report.findings[0].severity == "error"


def test_pix003_does_not_fire_when_allow_antialiasing() -> None:
    doc = _doc(validation={"allow_antialiasing": True})
    canvas = _canvas(8, 8, {(0, 0): BLEND_RED_BLACK})
    ctx = _ctx(doc, {("idle", "south", 0): canvas})
    report = run_validation(ctx, only=["PIX003"])
    assert report.findings == []


def test_pix003_does_not_fire_on_non_blend_foreign_colour() -> None:
    # OFF_PALETTE lies on no segment between two palette colours, so it isn't an AA
    # artifact by this rule's definition — that's PIX004's job, not PIX003's.
    doc = _doc()
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


def test_pix004_does_not_fire_on_blend_colour() -> None:
    # A genuine blend of two palette colours is PIX003's concern exclusively; PIX004
    # must not double-report it, with or without allow_antialiasing.
    doc = _doc()
    canvas = _canvas(8, 8, {(0, 0): BLEND_RED_BLACK})
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


def test_pix005_counts_declared_colours_not_expanded_ramp_tones() -> None:
    # api.py's validation path hands PIX rules the palette_for_polish-expanded
    # palette; the render-time derived ramp/outline tones must not count against
    # the authored palette's limit.
    doc = _doc(validation={"palette_limit": 2})
    expanded = resolve_palette(
        expand_palette(
            doc.palette.model_copy(update={"auto_ramp": True, "derive_outline": True})
        )
    )
    assert expanded.size > 2  # red/black each expanded to 3 tones + outline
    ctx = RuleContext(
        doc=doc,
        palette=expanded,
        frames={},
        resolved=resolve_frames(doc),
        tiles={},
    )
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


def test_pix007_fires_on_uninked_ground_shadow_band_without_polish_metadata() -> None:
    # A polished frame's contact-shadow band (rows appended below the sprite,
    # shadow-darkened, never inked) sits on the silhouette edge. Without the
    # RuleContext.polish_shadow_rows band info, those pixels read as a small
    # off-colour "outline" patch and the heuristic fires.
    doc = _doc(
        canvas=(10, 10),
        palette_colors=[
            {"id": "red", "hex": "#ff0000"},
            {"id": "black", "hex": "#000000"},
        ],
    )
    canvas = Canvas(10, 10)
    canvas.draw_rect((1, 1), (8, 8), RED, fill=True)
    # A narrow ground-shadow patch below the sprite's bottom edge: 2 of the 28
    # edge pixels (~7%) in a shadow-darkened colour the outline never inks.
    canvas.set_pixel(4, 9, (40, 40, 40, 255))
    canvas.set_pixel(5, 9, (40, 40, 40, 255))
    ctx = _ctx(doc, {("idle", "south", 0): canvas})
    report = run_validation(ctx, only=["PIX007"])
    assert len(report.findings) == 1
    assert report.findings[0].rule_id == "PIX007"
    assert report.findings[0].severity == "warning"


def test_pix007_stays_silent_when_ground_shadow_band_is_excluded() -> None:
    # Same frame, but with polish_shadow_rows=1 the shadow band is excluded
    # from the edge-coverage analysis: the silhouette edge is the sprite's own
    # inked ring (all RED here), so no off-colour patch is flagged.
    doc = _doc(
        canvas=(10, 10),
        palette_colors=[
            {"id": "red", "hex": "#ff0000"},
            {"id": "black", "hex": "#000000"},
        ],
    )
    canvas = Canvas(10, 10)
    canvas.draw_rect((1, 1), (8, 8), RED, fill=True)
    canvas.set_pixel(4, 9, (40, 40, 40, 255))
    canvas.set_pixel(5, 9, (40, 40, 40, 255))
    ctx = _ctx(
        doc,
        {("idle", "south", 0): canvas},
        polish_shadow_rows=1,
    )
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


# ---- PIX011: bitmap key references an unknown palette id --------------------------


def test_pix011_fires_on_unknown_palette_id_in_bitmap_key() -> None:
    doc = _doc(
        regions={
            "body": {
                "anchor": "root",
                "layer": 0,
                "shapes": [
                    {"op": "bitmap", "at": [0, 0], "key": {"o": "not_a_color"}, "rows": ["o"]}
                ],
            }
        }
    )
    ctx = _ctx(doc, {})
    report = run_validation(ctx, only=["PIX011"])
    assert len(report.findings) == 1
    assert report.findings[0].rule_id == "PIX011"
    assert report.findings[0].severity == "error"
    assert report.findings[0].measurements["char"] == "o"
    assert report.findings[0].measurements["color_id"] == "not_a_color"


def test_pix011_does_not_fire_on_known_palette_id_in_bitmap_key() -> None:
    doc = _doc(
        regions={
            "body": {
                "anchor": "root",
                "layer": 0,
                "shapes": [{"op": "bitmap", "at": [0, 0], "key": {"o": "black"}, "rows": ["o"]}],
            }
        }
    )
    ctx = _ctx(doc, {})
    report = run_validation(ctx, only=["PIX011"])
    assert report.findings == []


# ---- PIX012: flat-shading heuristic for bitmap art (heuristic) --------------------


_SKIN_RAMP = [
    {"id": "skin_light", "hex": "#ffcc99", "ramp": "skin"},
    {"id": "skin_dark", "hex": "#cc9966", "ramp": "skin"},
]


def _bitmap_region(rows: list[str]) -> dict[str, Any]:
    return {
        "anchor": "root",
        "layer": 0,
        "shapes": [{"op": "bitmap", "at": [0, 0], "key": {"l": "skin_light"}, "rows": rows}],
    }


def test_pix012_fires_on_large_flat_material() -> None:
    doc = _doc(palette_colors=_SKIN_RAMP, regions={"body": _bitmap_region(["l" * 8] * 8)})
    ctx = _ctx(doc, {})
    report = run_validation(ctx, only=["PIX012"])
    assert len(report.findings) == 1
    assert report.findings[0].rule_id == "PIX012"
    assert report.findings[0].severity == "warning"
    assert report.findings[0].measurements["ramp"] == "skin"
    assert report.findings[0].measurements["area_px"] == 64


def test_pix012_does_not_fire_on_small_flat_area() -> None:
    # A small flat patch (e.g. a shadow sliver) is a legitimate use of a single shade
    # and must not be flagged, even though it's still just one colour from the ramp.
    doc = _doc(palette_colors=_SKIN_RAMP, regions={"body": _bitmap_region(["l" * 4] * 4)})
    ctx = _ctx(doc, {})
    report = run_validation(ctx, only=["PIX012"])
    assert report.findings == []


def _rect_region(color_id: str) -> dict[str, Any]:
    return {
        "anchor": "root",
        "layer": 0,
        "shapes": [{"op": "rect", "color": color_id, "at": [0, 0], "size": [8, 8]}],
    }


_SKIN_LIGHT_RGBA: RGBA = (255, 204, 153, 255)  # #ffcc99
_SKIN_DARK_RGBA: RGBA = (204, 153, 102, 255)  # #cc9966


def test_pix012_fires_on_flat_rect_region() -> None:
    # A large rect shape painted in a single ramp colour has no bitmap keys, so
    # only the rendered-frame path (pixels on the composited canvas) can see it.
    doc = _doc(palette_colors=_SKIN_RAMP, regions={"body": _rect_region("skin_light")})
    canvas = _canvas(8, 8, {(x, y): _SKIN_LIGHT_RGBA for x in range(8) for y in range(8)})
    ctx = _ctx(doc, {("idle", "south", 0): canvas})
    report = run_validation(ctx, only=["PIX012"])
    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.rule_id == "PIX012"
    assert finding.measurements["ramp"] == "skin"
    assert finding.measurements["area_px"] == 64
    assert finding.animation == "idle"
    assert finding.direction == "south"
    assert finding.frame == 0


def test_pix012_does_not_fire_on_two_tone_region() -> None:
    # The same 64px surface split between two ramp colours is properly shaded
    # and must stay silent even though each half is one flat colour.
    doc = _doc(palette_colors=_SKIN_RAMP, regions={"body": _rect_region("skin_light")})
    canvas = _canvas(8, 8)
    for x in range(8):
        for y in range(8):
            canvas.set_pixel(x, y, _SKIN_DARK_RGBA if x < 4 else _SKIN_LIGHT_RGBA)
    ctx = _ctx(doc, {("idle", "south", 0): canvas})
    report = run_validation(ctx, only=["PIX012"])
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


# ---- PIX016: same-colour orphan pixel ---------------------------------------


def test_pix016_fires_on_same_colour_orphan() -> None:
    doc = _doc()
    ctx = _ctx(doc, {("idle", "south", 0): _canvas(8, 8, {(3, 3): RED})})
    report = run_validation(ctx, only=["PIX016"])
    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.rule_id == "PIX016"
    assert finding.severity == "warning"
    assert finding.kind == "heuristic"
    assert finding.measurements["coords"] == [[3, 3]]


def test_pix016_does_not_fire_when_same_colour_neighbour_exists() -> None:
    doc = _doc()
    pixels = {(2, 2): RED, (3, 2): RED, (2, 3): RED, (3, 3): RED}  # 2x2 block
    ctx = _ctx(doc, {("idle", "south", 0): _canvas(8, 8, pixels)})
    report = run_validation(ctx, only=["PIX016"])
    assert report.findings == []


def test_pix016_orthogonal_only_diagonal_same_colour_still_fires() -> None:
    # Diagonal same-colour neighbours do not count (orthogonal only), so a
    # diagonal line of one colour reads as two separate orphans.
    doc = _doc()
    ctx = _ctx(doc, {("idle", "south", 0): _canvas(8, 8, {(3, 3): RED, (4, 4): RED})})
    report = run_validation(ctx, only=["PIX016"])
    assert len(report.findings) == 1
    assert report.findings[0].measurements["orphan_count"] == 2


# ---- PIX017: noisy cluster ---------------------------------------------------


def test_pix017_fires_on_isolated_2px_cluster() -> None:
    doc = _doc()
    ctx = _ctx(doc, {("idle", "south", 0): _canvas(8, 8, {(2, 2): RED, (3, 2): RED})})
    report = run_validation(ctx, only=["PIX017"])
    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.rule_id == "PIX017"
    assert finding.severity == "info"
    assert finding.measurements["coords"] == [[2, 2], [3, 2]]


def test_pix017_fires_on_isolated_3px_L_cluster() -> None:
    doc = _doc()
    pixels = {(2, 2): RED, (3, 2): RED, (3, 3): RED}
    ctx = _ctx(doc, {("idle", "south", 0): _canvas(8, 8, pixels)})
    report = run_validation(ctx, only=["PIX017"])
    assert len(report.findings) == 1
    assert report.findings[0].measurements["cluster_count"] == 1


def test_pix017_does_not_fire_when_bbox_has_another_colour() -> None:
    # The L-cluster's bbox is filled by a black pixel -> not an isolated speck.
    doc = _doc()
    pixels = {(2, 2): RED, (3, 2): RED, (3, 3): RED, (2, 3): BLACK}
    ctx = _ctx(doc, {("idle", "south", 0): _canvas(8, 8, pixels)})
    report = run_validation(ctx, only=["PIX017"])
    assert report.findings == []


def test_pix017_does_not_fire_on_4px_block_or_single_pixel() -> None:
    doc = _doc()
    block = {(2, 2): RED, (3, 2): RED, (2, 3): RED, (3, 3): RED}
    ctx = _ctx(doc, {("idle", "south", 0): _canvas(8, 8, block)})
    assert run_validation(ctx, only=["PIX017"]).findings == []
    single = _ctx(doc, {("idle", "south", 0): _canvas(8, 8, {(4, 4): RED})})
    assert run_validation(single, only=["PIX017"]).findings == []


def test_pix016_and_pix017_stay_silent_on_polished_style_dither() -> None:
    # The render-polish pass deliberately dithers 1px orphans between shade
    # tones and forms 2-3px clusters that touch other colours. Both calibrated
    # heuristics must treat that as intentional art, not noise.
    doc = _doc()  # red + black palette
    pixels: dict[tuple[int, int], RGBA] = {}
    # A mid-tone field (red) with a few intentional 1px BLACK dither specks,
    # each touching red on at least one side (the polish shade-band signature).
    for y in range(2, 6):
        for x in range(2, 6):
            pixels[(x, y)] = RED
    for (x, y) in [(2, 3), (4, 3), (3, 5)]:
        pixels[(x, y)] = BLACK  # orphan dither: BLACK has no BLACK neighbour,
        # but its 3x3 DOES contain red -> not a defect speck under PIX016
    ctx = _ctx(doc, {("idle", "south", 0): _canvas(8, 8, pixels)})
    assert run_validation(ctx, only=["PIX016"]).findings == []
    # A 2px BLACK cluster touching red (polish cluster signature) is also fine.
    pixels2 = dict(pixels)
    pixels2[(5, 2)] = BLACK
    ctx2 = _ctx(doc, {("idle", "south", 0): _canvas(8, 8, pixels2)})
    assert run_validation(ctx2, only=["PIX017"]).findings == []


# ---- PIX018: broken outline ---------------------------------------------------


INK: RGBA = (34, 34, 34, 255)


def _outline_doc() -> CharacterAsset:
    return _doc(
        palette_colors=[
            {"id": "red", "hex": "#ff0000"},
            {"id": "ink", "hex": "#222222", "role": "outline"},
        ]
    )


def _outlined_rect(notch: set[tuple[int, int]] | None = None) -> Canvas:
    """6x6 rect at (0,0): ink boundary, red interior, optional ink-removed notch."""
    c = Canvas(8, 8)
    for x in range(6):
        for y in range(6):
            on_boundary = x in (0, 5) or y in (0, 5)
            c.set_pixel(x, y, INK if on_boundary else RED)
    for (x, y) in notch or set():
        c.set_pixel(x, y, RED)
    return c


def test_pix018_fires_on_two_pixel_outline_gap() -> None:
    doc = _outline_doc()
    canvas = _outlined_rect(notch={(1, 0), (2, 0)})
    ctx = _ctx(doc, {("idle", "south", 0): canvas})
    report = run_validation(ctx, only=["PIX018"])
    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.rule_id == "PIX018"
    assert finding.severity == "warning"
    assert finding.measurements["gap_count"] == 1
    assert finding.measurements["coords"] == [[1, 0], [2, 0]]


def test_pix018_does_not_fire_on_closed_outline() -> None:
    doc = _outline_doc()
    ctx = _ctx(doc, {("idle", "south", 0): _outlined_rect()})
    report = run_validation(ctx, only=["PIX018"])
    assert report.findings == []


def test_pix018_does_not_fire_on_single_pixel_gap() -> None:
    doc = _outline_doc()
    ctx = _ctx(doc, {("idle", "south", 0): _outlined_rect(notch={(1, 0)})})
    report = run_validation(ctx, only=["PIX018"])
    assert report.findings == []


def test_pix018_skips_when_no_outline_colour_declared() -> None:
    doc = _doc()  # default palette: red + black, no outline role
    ctx = _ctx(doc, {("idle", "south", 0): _outlined_rect(notch={(1, 0), (2, 0)})})
    report = run_validation(ctx, only=["PIX018"])
    assert report.findings == []


# ---- PIX019: spatial banding on 45-degree slopes ------------------------------


def test_pix019_fires_on_undithered_six_pixel_diagonal() -> None:
    doc = _doc()
    # A thin 1px BLACK diagonal line cutting through a flat RED field: the run's
    # colour differs from the field's dominant colour, so it reads as a hard
    # tone step — genuine banding. (A same-colour run in a same-colour field —
    # flat fill — must NOT fire; covered by the solid-rect stays-silent case.)
    pixels: dict[tuple[int, int], RGBA] = {(x, y): RED for y in range(16) for x in range(16)}
    for x in range(2, 8):
        pixels[(x, x)] = BLACK
    ctx = _ctx(doc, {("idle", "south", 0): _canvas(16, 16, pixels)})
    report = run_validation(ctx, only=["PIX019"])
    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.rule_id == "PIX019"
    assert finding.severity == "info"
    assert finding.measurements["run_count"] >= 1


def test_pix019_fires_on_anti_diagonal_run() -> None:
    doc = _doc()
    # Same thin tone step along the anti-diagonal.
    pixels: dict[tuple[int, int], RGBA] = {(x, y): RED for y in range(16) for x in range(16)}
    for (x, y) in [(2, 12), (3, 11), (4, 10), (5, 9), (6, 8), (7, 7)]:
        pixels[(x, y)] = BLACK
    ctx = _ctx(doc, {("idle", "south", 0): _canvas(16, 16, pixels)})
    report = run_validation(ctx, only=["PIX019"])
    assert len(report.findings) == 1
    assert report.findings[0].measurements["run_count"] >= 1


def test_pix019_does_not_fire_on_solid_flat_fill() -> None:
    # A solid single-colour rect: the run's colour == the field's colour, so it
    # is flat fill, not banding. This is the critic's false-positive probe.
    doc = _doc()
    pixels: dict[tuple[int, int], RGBA] = {(x, y): RED for y in range(16) for x in range(16)}
    ctx = _ctx(doc, {("idle", "south", 0): _canvas(16, 16, pixels)})
    report = run_validation(ctx, only=["PIX019"])
    assert report.findings == []


def test_pix019_does_not_fire_on_short_or_axis_aligned_runs() -> None:
    doc = _doc()
    # A small 5x5 RED block: its own interior diagonals cap at 4px, and a 3px
    # BLACK diagonal inside stays under the 6px threshold.
    pixels: dict[tuple[int, int], RGBA] = {(x, y): RED for y in range(5) for x in range(5)}
    for x in range(1, 4):
        pixels[(x, x)] = BLACK
    ctx = _ctx(doc, {("idle", "south", 0): _canvas(8, 8, pixels)})
    assert run_validation(ctx, only=["PIX019"]).findings == []
    horizontal: dict[tuple[int, int], RGBA] = {(x, y): RED for y in range(8) for x in range(8)}
    for x in range(8):  # axis-aligned, not 45 degrees
        horizontal[(x, 3)] = BLACK
    ctx2 = _ctx(doc, {("idle", "south", 0): _canvas(8, 8, horizontal)})
    assert run_validation(ctx2, only=["PIX019"]).findings == []


def test_pix019_does_not_fire_on_silhouette_edge_diagonal() -> None:
    # A lone diagonal IS its own silhouette — every pixel is an edge pixel, so
    # the flat-context criterion correctly stays silent (this is the shape's
    # 45-degree outline, not shading banding).
    doc = _doc()
    pixels = {(x, x): RED for x in range(8)}
    ctx = _ctx(doc, {("idle", "south", 0): _canvas(8, 8, pixels)})
    report = run_validation(ctx, only=["PIX019"])
    assert report.findings == []


def test_pix019_does_not_fire_on_dithered_diagonal() -> None:
    # A small 5x5 block with a dithered diagonal: alternate colours break the
    # same-colour run, and the block's own diagonals stay under 6px.
    doc = _doc()
    pixels: dict[tuple[int, int], RGBA] = {(x, y): RED for y in range(5) for x in range(5)}
    for x in range(1, 5):
        pixels[(x, x)] = BLACK if x % 2 == 0 else RED
    ctx = _ctx(doc, {("idle", "south", 0): _canvas(8, 8, pixels)})
    report = run_validation(ctx, only=["PIX019"])
    assert report.findings == []


def test_pix019_does_not_fire_on_polished_expanded_palette() -> None:
    # The render-polish pass deliberately shades with integer run-distance tone
    # bands, so PIX019 (an authored-art check) must stay silent when the frames
    # were rendered through polish — signalled by the palette_for_polish
    # expansion (auto_ramp + derive_outline). A staircase that WOULD fire on a
    # flat palette must not fire against the expanded palette.
    doc = _doc()  # red + black declared palette
    pixels: dict[tuple[int, int], RGBA] = {(x, y): RED for y in range(16) for x in range(16)}
    for x in range(2, 8):
        pixels[(x, x)] = BLACK  # tone step: fires under the flat palette
    from pixel_forge.domain.palette import palette_for_polish

    expanded = resolve_palette(palette_for_polish(doc.palette))
    assert len(expanded.palette.colors) > len(doc.palette.colors)  # polish signalled
    ctx = _ctx(
        doc,
        {("idle", "south", 0): _canvas(16, 16, pixels)},
        palette_override=expanded,
    )
    report = run_validation(ctx, only=["PIX019"])
    assert report.findings == []


def test_pix019_anti_diagonal_coords_are_staircase_pixels() -> None:
    # The anti-diagonal fires-test must localise the actual tone-step pixels,
    # not the surrounding field (round-1 critic: coords were 100% field).
    doc = _doc()
    pixels: dict[tuple[int, int], RGBA] = {(x, y): RED for y in range(16) for x in range(16)}
    staircase = [(2, 12), (3, 11), (4, 10), (5, 9), (6, 8), (7, 7)]
    for (x, y) in staircase:
        pixels[(x, y)] = BLACK
    ctx = _ctx(doc, {("idle", "south", 0): _canvas(16, 16, pixels)})
    report = run_validation(ctx, only=["PIX019"])
    assert len(report.findings) == 1
    coords = {tuple(c) for c in report.findings[0].measurements["coords"]}
    assert all(c in coords for c in staircase)


# ---- PIX020: weak silhouette ---------------------------------------------------


def test_pix020_fires_on_sparse_silhouette() -> None:
    doc = _doc()
    pixels = {(x, x): RED for x in range(6)}  # 6px diagonal: fill 6/36 = 0.17
    ctx = _ctx(doc, {("idle", "south", 0): _canvas(8, 8, pixels)})
    report = run_validation(ctx, only=["PIX020"])
    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.rule_id == "PIX020"
    assert finding.severity == "info"
    assert finding.measurements["coords"] == [[0, 0], [5, 0], [0, 5], [5, 5]]


def test_pix020_fires_on_off_centre_mass() -> None:
    doc = _doc()
    pixels = {(x, y): RED for x in range(4) for y in range(4)}  # 4x4 block
    pixels[(7, 7)] = RED  # lone pixel drags the centroid off centre
    ctx = _ctx(doc, {("idle", "south", 0): _canvas(8, 8, pixels)})
    report = run_validation(ctx, only=["PIX020"])
    assert len(report.findings) == 1
    assert report.findings[0].measurements["off_centre_ratio"] > 0.30
    assert report.findings[0].measurements["fill_ratio"] >= 0.25


def test_pix020_does_not_fire_on_centred_solid_shape() -> None:
    doc = _doc()
    pixels = {(x, y): RED for x in range(1, 7) for y in range(1, 7)}  # 6x6 centred
    ctx = _ctx(doc, {("idle", "south", 0): _canvas(8, 8, pixels)})
    report = run_validation(ctx, only=["PIX020"])
    assert report.findings == []
