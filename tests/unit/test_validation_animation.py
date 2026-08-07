from __future__ import annotations

from typing import Any

from pixel_forge.animation.resolver import ResolvedFrame, resolve_frames
from pixel_forge.domain.palette import resolve_palette
from pixel_forge.rendering.canvas import RGBA, Canvas
from pixel_forge.schemas import CharacterAsset, RegionTransform, parse_asset_doc
from pixel_forge.validation.engine import RuleContext, run_validation

RED: RGBA = (255, 0, 0, 255)


def _doc(
    *,
    canvas: tuple[int, int] = (8, 8),
    baseline_y: int | None = None,
    directions: list[str] | None = None,
    anchors: dict[str, list[int]] | None = None,
    regions: dict[str, Any] | None = None,
    animations: dict[str, Any] | None = None,
    validation: dict[str, Any] | None = None,
    palette_colors: list[dict[str, Any]] | None = None,
) -> CharacterAsset:
    if directions is None:
        directions = ["south"]
    if anchors is None:
        anchors = {"root": [0, 0]}
    if regions is None:
        regions = {"body": {"anchor": "root", "layer": 0, "shapes": []}}
    if animations is None:
        animations = {
            "idle": {"loop": True, "frames": [{"duration_ms": 100, "events": [], "transforms": {}}]}
        }
    if palette_colors is None:
        palette_colors = [{"id": "red", "hex": "#ff0000"}]
    data = {
        "schema_version": 1,
        "asset": {
            "id": "hero",
            "type": "character",
            "canvas": list(canvas),
            "baseline_y": baseline_y,
        },
        "palette": {"id": "p", "colors": palette_colors},
        "directions": directions,
        "mirror": {},
        "anchors": anchors,
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


def _canvas_with_n_opaque(w: int, h: int, n: int) -> Canvas:
    c = Canvas(w, h)
    count = 0
    for y in range(h):
        for x in range(w):
            if count >= n:
                return c
            c.set_pixel(x, y, RED)
            count += 1
    return c


def _ctx(
    doc: CharacterAsset,
    frames: dict[tuple[str, str, int], Canvas],
    resolved: list[ResolvedFrame] | None = None,
) -> RuleContext:
    return RuleContext(
        doc=doc,
        palette=resolve_palette(doc.palette),
        frames=frames,
        resolved=resolved if resolved is not None else resolve_frames(doc),
        tiles={},
    )


# ---- ANI001: baseline drift --------------------------------------------------------


def test_ani001_fires_on_baseline_drift() -> None:
    doc = _doc(baseline_y=6)
    canvas = _canvas(8, 8, {(0, 3): RED})  # lowest opaque row = 3
    ctx = _ctx(doc, {("idle", "south", 0): canvas})
    report = run_validation(ctx, only=["ANI001"])
    assert len(report.findings) == 1
    assert report.findings[0].rule_id == "ANI001"
    assert report.findings[0].severity == "error"


def test_ani001_does_not_fire_when_baseline_matches() -> None:
    doc = _doc(baseline_y=6)
    canvas = _canvas(8, 8, {(0, 6): RED})  # lowest opaque row = 6
    ctx = _ctx(doc, {("idle", "south", 0): canvas})
    report = run_validation(ctx, only=["ANI001"])
    assert report.findings == []


# ---- ANI002: foot-anchor drift -----------------------------------------------------


def _foot_anchor_doc(*, frame1_offset: list[int]) -> CharacterAsset:
    return _doc(
        anchors={"feet": [0, 0], "root": [0, 0]},
        regions={
            "feet_region": {"anchor": "feet", "layer": 0, "shapes": []},
            "body": {"anchor": "root", "layer": 1, "shapes": []},
        },
        animations={
            "walk": {
                "loop": True,
                "frames": [
                    {
                        "duration_ms": 100,
                        "events": [],
                        "transforms": {"feet_region": {"offset": [0, 0]}},
                    },
                    {
                        "duration_ms": 100,
                        "events": [],
                        "transforms": {"feet_region": {"offset": frame1_offset}},
                    },
                ],
            }
        },
    )


def test_ani002_fires_on_foot_anchor_drift() -> None:
    doc = _foot_anchor_doc(frame1_offset=[3, 0])
    ctx = _ctx(doc, {})
    report = run_validation(ctx, only=["ANI002"])
    assert len(report.findings) == 1
    assert report.findings[0].rule_id == "ANI002"
    assert report.findings[0].severity == "error"
    assert report.findings[0].region == "feet_region"


def test_ani002_does_not_fire_when_anchor_stable() -> None:
    doc = _foot_anchor_doc(frame1_offset=[0, 0])
    ctx = _ctx(doc, {})
    report = run_validation(ctx, only=["ANI002"])
    assert report.findings == []


# ---- ANI003: pivot drift -----------------------------------------------------------


def test_ani003_fires_on_large_centre_shift_for_looping_animation() -> None:
    doc = _doc(
        canvas=(10, 10),
        animations={
            "walk": {
                "loop": True,
                "frames": [
                    {"duration_ms": 100, "events": [], "transforms": {}},
                    {"duration_ms": 100, "events": [], "transforms": {}},
                ],
            }
        },
    )
    frame0 = _canvas(10, 10, {(0, 0): RED, (1, 0): RED})  # bbox cx = 0.5
    frame1 = _canvas(10, 10, {(5, 0): RED, (6, 0): RED})  # bbox cx = 5.5
    ctx = _ctx(doc, {("walk", "south", 0): frame0, ("walk", "south", 1): frame1})
    report = run_validation(ctx, only=["ANI003"])
    assert len(report.findings) == 1
    assert report.findings[0].rule_id == "ANI003"
    assert report.findings[0].severity == "error"


def test_ani003_does_not_fire_on_small_centre_shift() -> None:
    doc = _doc(
        canvas=(10, 10),
        animations={
            "walk": {
                "loop": True,
                "frames": [
                    {"duration_ms": 100, "events": [], "transforms": {}},
                    {"duration_ms": 100, "events": [], "transforms": {}},
                ],
            }
        },
    )
    frame0 = _canvas(10, 10, {(0, 0): RED, (1, 0): RED})  # bbox cx = 0.5
    frame1 = _canvas(10, 10, {(1, 0): RED, (2, 0): RED})  # bbox cx = 1.5
    ctx = _ctx(doc, {("walk", "south", 0): frame0, ("walk", "south", 1): frame1})
    report = run_validation(ctx, only=["ANI003"])
    assert report.findings == []


# ---- ANI004: attachment-anchor drift -----------------------------------------------


def _attachment_doc(*, frame1_transforms: dict[str, Any]) -> CharacterAsset:
    return _doc(
        anchors={"hand": [2, 2], "feet": [0, 0]},
        regions={
            "weapon": {"anchor": "hand", "layer": 0, "shapes": []},
            "feet_region": {"anchor": "feet", "layer": 1, "shapes": []},
        },
        animations={
            "attack": {
                "loop": False,
                "frames": [
                    {"duration_ms": 100, "events": [], "transforms": {}},
                    {"duration_ms": 100, "events": [], "transforms": frame1_transforms},
                ],
            }
        },
    )


def _attachment_resolved(*, frame1_offset: tuple[int, int]) -> list[ResolvedFrame]:
    empty = RegionTransform()
    return [
        ResolvedFrame(
            direction="south",
            animation="attack",
            index=0,
            duration_ms=100,
            events=(),
            transforms={"weapon": RegionTransform(offset=(0, 0)), "feet_region": empty},
            mirrored_from=None,
        ),
        ResolvedFrame(
            direction="south",
            animation="attack",
            index=1,
            duration_ms=100,
            events=(),
            transforms={"weapon": RegionTransform(offset=frame1_offset), "feet_region": empty},
            mirrored_from=None,
        ),
    ]


def test_ani004_fires_when_anchor_moves_without_explicit_transform() -> None:
    doc = _attachment_doc(frame1_transforms={})
    resolved = _attachment_resolved(frame1_offset=(5, 0))
    ctx = _ctx(doc, {}, resolved=resolved)
    report = run_validation(ctx, only=["ANI004"])
    assert len(report.findings) == 1
    assert report.findings[0].rule_id == "ANI004"
    assert report.findings[0].severity == "error"
    assert report.findings[0].region == "weapon"


def test_ani004_does_not_fire_when_move_is_explicit() -> None:
    doc = _attachment_doc(frame1_transforms={"weapon": {"offset": [5, 0]}})
    resolved = _attachment_resolved(frame1_offset=(5, 0))
    ctx = _ctx(doc, {}, resolved=resolved)
    report = run_validation(ctx, only=["ANI004"])
    assert report.findings == []


# ---- ANI005: loop popping (heuristic) ----------------------------------------------


def test_ani005_fires_on_large_first_last_frame_difference() -> None:
    doc = _doc(
        canvas=(10, 10),
        animations={
            "cycle": {
                "loop": True,
                "frames": [
                    {"duration_ms": 100, "events": [], "transforms": {}},
                    {"duration_ms": 100, "events": [], "transforms": {}},
                ],
            }
        },
    )
    first = _canvas_with_n_opaque(10, 10, 10)
    last = _canvas(10, 10)  # empty: 100% different
    ctx = _ctx(doc, {("cycle", "south", 0): first, ("cycle", "south", 1): last})
    report = run_validation(ctx, only=["ANI005"])
    assert len(report.findings) == 1
    assert report.findings[0].rule_id == "ANI005"
    assert report.findings[0].severity == "warning"


def test_ani005_does_not_fire_when_first_and_last_match() -> None:
    doc = _doc(
        canvas=(10, 10),
        animations={
            "cycle": {
                "loop": True,
                "frames": [
                    {"duration_ms": 100, "events": [], "transforms": {}},
                    {"duration_ms": 100, "events": [], "transforms": {}},
                ],
            }
        },
    )
    first = _canvas_with_n_opaque(10, 10, 10)
    last = _canvas_with_n_opaque(10, 10, 10)
    ctx = _ctx(doc, {("cycle", "south", 0): first, ("cycle", "south", 1): last})
    report = run_validation(ctx, only=["ANI005"])
    assert report.findings == []


# ---- ANI006: palette flicker (heuristic) --------------------------------------------


def _three_frame_doc() -> CharacterAsset:
    return _doc(
        canvas=(4, 4),
        animations={
            "idle": {
                "loop": True,
                "frames": [
                    {"duration_ms": 100, "events": [], "transforms": {}},
                    {"duration_ms": 100, "events": [], "transforms": {}},
                    {"duration_ms": 100, "events": [], "transforms": {}},
                ],
            }
        },
    )


def test_ani006_fires_on_flickering_colour() -> None:
    doc = _three_frame_doc()
    blue: RGBA = (0, 0, 255, 255)
    frame0 = _canvas(4, 4, {(0, 0): RED, (1, 1): blue})
    frame1 = _canvas(4, 4, {(0, 0): RED})
    frame2 = _canvas(4, 4, {(0, 0): RED, (1, 1): blue})
    ctx = _ctx(
        doc,
        {("idle", "south", 0): frame0, ("idle", "south", 1): frame1, ("idle", "south", 2): frame2},
    )
    report = run_validation(ctx, only=["ANI006"])
    assert len(report.findings) == 1
    assert report.findings[0].rule_id == "ANI006"
    assert report.findings[0].severity == "warning"
    assert report.findings[0].frame == 1


def test_ani006_does_not_fire_when_colour_present_throughout() -> None:
    doc = _three_frame_doc()
    blue: RGBA = (0, 0, 255, 255)
    frame0 = _canvas(4, 4, {(0, 0): RED, (1, 1): blue})
    frame1 = _canvas(4, 4, {(0, 0): RED, (1, 1): blue})
    frame2 = _canvas(4, 4, {(0, 0): RED, (1, 1): blue})
    ctx = _ctx(
        doc,
        {("idle", "south", 0): frame0, ("idle", "south", 1): frame1, ("idle", "south", 2): frame2},
    )
    report = run_validation(ctx, only=["ANI006"])
    assert report.findings == []


# ---- ANI007: declared animation / rendered frame must exist -------------------------


def test_ani007_fires_on_missing_rendered_frame() -> None:
    doc = _doc(
        animations={
            "idle": {
                "loop": True,
                "frames": [
                    {"duration_ms": 100, "events": [], "transforms": {}},
                    {"duration_ms": 100, "events": [], "transforms": {}},
                ],
            }
        }
    )
    ctx = _ctx(doc, {("idle", "south", 0): _canvas(8, 8, {(0, 0): RED})})  # index 1 missing
    report = run_validation(ctx, only=["ANI007"])
    assert len(report.findings) == 1
    assert report.findings[0].rule_id == "ANI007"
    assert report.findings[0].severity == "error"
    assert report.findings[0].frame == 1


def test_ani007_does_not_fire_when_all_frames_rendered() -> None:
    doc = _doc(
        animations={
            "idle": {
                "loop": True,
                "frames": [
                    {"duration_ms": 100, "events": [], "transforms": {}},
                    {"duration_ms": 100, "events": [], "transforms": {}},
                ],
            }
        }
    )
    ctx = _ctx(
        doc,
        {
            ("idle", "south", 0): _canvas(8, 8, {(0, 0): RED}),
            ("idle", "south", 1): _canvas(8, 8, {(0, 0): RED}),
        },
    )
    report = run_validation(ctx, only=["ANI007"])
    assert report.findings == []


# ---- ANI008: unexpected silhouette-volume change (heuristic) ------------------------


def test_ani008_fires_on_large_volume_change() -> None:
    doc = _doc(
        canvas=(10, 10),
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
    frame0 = _canvas_with_n_opaque(10, 10, 10)
    frame1 = _canvas_with_n_opaque(10, 10, 20)  # +100%
    ctx = _ctx(doc, {("idle", "south", 0): frame0, ("idle", "south", 1): frame1})
    report = run_validation(ctx, only=["ANI008"])
    assert len(report.findings) == 1
    assert report.findings[0].rule_id == "ANI008"
    assert report.findings[0].severity == "warning"


def test_ani008_does_not_fire_on_small_volume_change() -> None:
    doc = _doc(
        canvas=(10, 10),
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
    frame0 = _canvas_with_n_opaque(10, 10, 10)
    frame1 = _canvas_with_n_opaque(10, 10, 12)  # +20%
    ctx = _ctx(doc, {("idle", "south", 0): frame0, ("idle", "south", 1): frame1})
    report = run_validation(ctx, only=["ANI008"])
    assert report.findings == []


# ---- ANI009: directional inconsistency -----------------------------------------------


def _ani009_resolved(*, south_frame_count: int) -> list[ResolvedFrame]:
    north = [
        ResolvedFrame(
            direction="north",
            animation="idle",
            index=i,
            duration_ms=100,
            events=(),
            transforms={},
            mirrored_from=None,
        )
        for i in range(2)
    ]
    south = [
        ResolvedFrame(
            direction="south",
            animation="idle",
            index=i,
            duration_ms=100,
            events=(),
            transforms={},
            mirrored_from=None,
        )
        for i in range(south_frame_count)
    ]
    return north + south


def test_ani009_fires_on_frame_count_mismatch_across_directions() -> None:
    doc = _doc()
    ctx = _ctx(doc, {}, resolved=_ani009_resolved(south_frame_count=1))
    report = run_validation(ctx, only=["ANI009"])
    assert len(report.findings) == 1
    assert report.findings[0].rule_id == "ANI009"
    assert report.findings[0].severity == "error"


def test_ani009_does_not_fire_when_directions_agree() -> None:
    doc = _doc()
    ctx = _ctx(doc, {}, resolved=_ani009_resolved(south_frame_count=2))
    report = run_validation(ctx, only=["ANI009"])
    assert report.findings == []


# ---- ANI010: inconsistent outline thickness ----------------------------------


def _outline_anim_doc(frame_count: int = 2) -> CharacterAsset:
    return _doc(
        canvas=(12, 12),
        palette_colors=[
            {"id": "red", "hex": "#ff0000"},
            {"id": "ink", "hex": "#222222", "role": "outline"},
        ],
        animations={
            "idle": {
                "loop": True,
                "frames": [
                    {"duration_ms": 100, "events": [], "transforms": {}}
                    for _ in range(frame_count)
                ],
            }
        },
    )


def _rect_outline_12(thickness: int) -> Canvas:
    """12x12 rect at (0,0): `thickness`-px ink outline ring, red interior."""
    c = Canvas(12, 12)
    for x in range(12):
        for y in range(12):
            on_boundary = x in (0, 11) or y in (0, 11)
            if thickness == 1:
                c.set_pixel(x, y, INK if on_boundary else RED)
            else:
                near_boundary = x < 3 or x > 8 or y < 3 or y > 8
                c.set_pixel(x, y, INK if near_boundary else RED)
    return c


INK: RGBA = (34, 34, 34, 255)


def test_ani010_fires_on_outline_thickness_variation() -> None:
    doc = _outline_anim_doc()
    frame0 = _rect_outline_12(1)  # 1px outline
    frame1 = _rect_outline_12(3)  # 3px outline
    ctx = _ctx(doc, {("idle", "south", 0): frame0, ("idle", "south", 1): frame1})
    report = run_validation(ctx, only=["ANI010"])
    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.rule_id == "ANI010"
    assert finding.severity == "warning"
    assert finding.kind == "heuristic"
    assert finding.measurements["min_thickness_px"] == 1
    assert finding.measurements["max_thickness_px"] == 3
    assert finding.measurements["coords"]  # edge pixels of the thinnest frame


def test_ani010_does_not_fire_when_outline_thickness_constant() -> None:
    doc = _outline_anim_doc()
    frame0 = _rect_outline_12(1)
    frame1 = _rect_outline_12(1)
    ctx = _ctx(doc, {("idle", "south", 0): frame0, ("idle", "south", 1): frame1})
    report = run_validation(ctx, only=["ANI010"])
    assert report.findings == []


def test_ani010_skips_when_no_outline_colour_declared() -> None:
    doc = _doc(canvas=(12, 12))  # red-only palette
    frame0 = _rect_outline_12(1)
    frame1 = _rect_outline_12(3)
    ctx = _ctx(doc, {("idle", "south", 0): frame0, ("idle", "south", 1): frame1})
    report = run_validation(ctx, only=["ANI010"])
    assert report.findings == []


# ---- ANI011: animation jitter -------------------------------------------------


def _jitter_doc(offsets: list[list[int]], durations: list[int] | None = None) -> CharacterAsset:
    if durations is None:
        durations = [100] * len(offsets)
    frames = [
        {"duration_ms": ms, "events": [], "transforms": {"arm": {"offset": off}}}
        for off, ms in zip(offsets, durations, strict=True)
    ]
    return _doc(
        anchors={"root": [0, 0]},
        regions={"arm": {"anchor": "root", "layer": 0, "shapes": []}},
        animations={"idle": {"loop": True, "frames": frames}},
    )


def test_ani011_fires_on_jump_out_and_back() -> None:
    doc = _jitter_doc(offsets=[[0, 0], [4, 0], [0, 0]])
    ctx = _ctx(doc, {})
    report = run_validation(ctx, only=["ANI011"])
    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.rule_id == "ANI011"
    assert finding.severity == "warning"
    assert finding.frame == 1
    assert finding.region == "arm"
    assert finding.measurements["coords"] == [[0, 0], [4, 0], [0, 0]]


def test_ani011_does_not_fire_on_smooth_small_steps() -> None:
    doc = _jitter_doc(offsets=[[0, 0], [1, 0], [2, 0]])
    ctx = _ctx(doc, {})
    report = run_validation(ctx, only=["ANI011"])
    assert report.findings == []


def test_ani011_does_not_fire_on_slow_animations() -> None:
    doc = _jitter_doc(offsets=[[0, 0], [4, 0], [0, 0]], durations=[200, 200, 200])
    ctx = _ctx(doc, {})
    report = run_validation(ctx, only=["ANI011"])
    assert report.findings == []


def test_ani011_does_not_fire_on_two_frame_animation() -> None:
    doc = _jitter_doc(offsets=[[0, 0], [4, 0]])
    ctx = _ctx(doc, {})
    report = run_validation(ctx, only=["ANI011"])
    assert report.findings == []


# ---- ANI012: shifting body volume ---------------------------------------------


def _volume_doc(loop: bool, counts: list[int]) -> CharacterAsset:
    frames = [
        {"duration_ms": 100, "events": [], "transforms": {}} for _ in counts
    ]
    return _doc(
        canvas=(10, 10),
        animations={"idle": {"loop": loop, "frames": frames}},
    )


def test_ani012_fires_on_large_volume_swing_in_loop() -> None:
    doc = _volume_doc(loop=True, counts=[20, 30])
    frame0 = _canvas_with_n_opaque(10, 10, 20)
    frame1 = _canvas_with_n_opaque(10, 10, 30)
    ctx = _ctx(doc, {("idle", "south", 0): frame0, ("idle", "south", 1): frame1})
    report = run_validation(ctx, only=["ANI012"])
    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.rule_id == "ANI012"
    assert finding.severity == "warning"
    assert finding.kind == "deterministic"
    assert finding.frame == 1
    assert finding.measurements["previous_count"] == 20
    assert finding.measurements["current_count"] == 30
    assert finding.measurements["coords"]  # pixels that appeared between frames


def test_ani012_does_not_fire_on_small_volume_change() -> None:
    doc = _volume_doc(loop=True, counts=[20, 21])
    frame0 = _canvas_with_n_opaque(10, 10, 20)
    frame1 = _canvas_with_n_opaque(10, 10, 21)
    ctx = _ctx(doc, {("idle", "south", 0): frame0, ("idle", "south", 1): frame1})
    report = run_validation(ctx, only=["ANI012"])
    assert report.findings == []


def test_ani012_does_not_fire_on_non_looping_animation() -> None:
    doc = _volume_doc(loop=False, counts=[20, 30])
    frame0 = _canvas_with_n_opaque(10, 10, 20)
    frame1 = _canvas_with_n_opaque(10, 10, 30)
    ctx = _ctx(doc, {("idle", "south", 0): frame0, ("idle", "south", 1): frame1})
    report = run_validation(ctx, only=["ANI012"])
    assert report.findings == []
