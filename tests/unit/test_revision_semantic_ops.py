"""Tests for the W3-B semantic editing operations (revisions/operations.py).

Every operation gets the two-test contract from AGENTS.md: applying it produces
the documented spec change, and applying the returned inverse restores the
original document byte-exactly (content_hash equality). Also covers the
protection guards (protected regions and op.protect) and one end-to-end pass:
add_component -> swap_palette -> change_pose on a fresh doc validates with zero
blocking findings.
"""

from __future__ import annotations

import pytest

from pixel_forge.animation.resolver import resolve_frames
from pixel_forge.domain.hashing import content_hash
from pixel_forge.domain.palette import resolve_palette
from pixel_forge.errors import OperationError
from pixel_forge.rendering import LocalRenderBackend, render_asset_frames
from pixel_forge.revisions.operations import apply_operation
from pixel_forge.schemas.animation import AnimationSpec, FrameSpec
from pixel_forge.schemas.asset import AssetHeader, CharacterAsset, ExportOptions, ValidationOptions
from pixel_forge.schemas.common import EllipseShape, RectShape, Region
from pixel_forge.schemas.palette import Palette, PaletteColor
from pixel_forge.schemas.revision import OperationSpec
from pixel_forge.schemas.validation import ValidationReport
from pixel_forge.validation.engine import RuleContext, run_validation


def make_doc() -> CharacterAsset:
    """A W3-B-friendly sprite doc: 32x32, single direction, body/head/arm
    regions (so change_pose's role discovery drives them), a protected `shield`
    region, and a 2-frame idle loop."""
    return CharacterAsset(
        schema_version=1,
        asset=AssetHeader(id="hero", type="character", canvas=(32, 32)),
        palette=Palette(
            id="hero_palette",
            colors=[
                PaletteColor(id="red", hex="#cc3333"),
                PaletteColor(id="blue", hex="#3366cc"),
                PaletteColor(id="green", hex="#33aa44"),
                PaletteColor(id="black", hex="#222222"),
                PaletteColor(id="outline", hex="#14100f", role="outline"),
            ],
        ),
        export=ExportOptions(),
        validation=ValidationOptions(),
        directions=["south"],
        anchors={"root": (16, 16)},
        regions={
            "torso": Region(
                anchor="root",
                layer=0,
                shapes=[RectShape(op="rect", color="red", at=(-4, -6), size=(8, 10))],
            ),
            "head": Region(
                anchor="root",
                layer=1,
                shapes=[EllipseShape(op="ellipse", color="blue", at=(-3, -12), size=(6, 6))],
            ),
            "arm_left": Region(
                anchor="root",
                layer=2,
                shapes=[RectShape(op="rect", color="green", at=(-7, -6), size=(3, 8))],
            ),
            "arm_right": Region(
                anchor="root",
                layer=3,
                shapes=[RectShape(op="rect", color="green", at=(4, -6), size=(3, 8))],
            ),
            "shield": Region(
                anchor="root",
                layer=4,
                shapes=[RectShape(op="rect", color="black", at=(2, 2), size=(4, 4))],
                protected=True,
            ),
        },
        animations={
            "idle": AnimationSpec(
                loop=True,
                frames=[FrameSpec(duration_ms=100), FrameSpec(duration_ms=100)],
            ),
        },
    )


def make_material_doc() -> CharacterAsset:
    """A sprite doc whose palette is tuned so `rusty_iron` deterministically
    remaps `base` onto `rust` (nearest-RGB after the transform), proving
    apply_material's per-region colour remap."""
    return CharacterAsset(
        schema_version=1,
        asset=AssetHeader(id="hero", type="character", canvas=(32, 32)),
        palette=Palette(
            id="mat_palette",
            colors=[
                PaletteColor(id="base", hex="#cc6600"),
                PaletteColor(id="rust", hex="#aa7a10"),
                PaletteColor(id="other_a", hex="#3366cc"),
                PaletteColor(id="other_b", hex="#33aa44"),
            ],
        ),
        export=ExportOptions(),
        validation=ValidationOptions(),
        directions=["south"],
        anchors={"root": (16, 16)},
        regions={
            "torso": Region(
                anchor="root",
                layer=0,
                shapes=[RectShape(op="rect", color="base", at=(-4, -6), size=(8, 10))],
            ),
            "head": Region(
                anchor="root",
                layer=1,
                shapes=[EllipseShape(op="ellipse", color="other_a", at=(-3, -12), size=(6, 6))],
            ),
        },
        animations={"idle": AnimationSpec(frames=[FrameSpec(duration_ms=100)])},
    )


def make_bitmap_doc() -> CharacterAsset:
    """`make_doc()` plus an `art` region with a bitmap shape whose outline ring
    has a 1px gap (the middle cell of the middle row is fill-coloured, flanked
    by outline cells above/below and beside it) — repair_outline's target."""
    doc = make_doc()
    data = doc.model_dump(mode="json")
    data["regions"]["art"] = {
        "anchor": "root",
        "layer": 5,
        "shapes": [
            {
                "op": "bitmap",
                "at": [0, 0],
                "key": {"o": "outline", "x": "red"},
                "rows": ["ooo", "oxx", "ooo"],
            }
        ],
    }
    return CharacterAsset.model_validate(data)


def _validate(doc: CharacterAsset) -> ValidationReport:
    all_frames = render_asset_frames(doc, LocalRenderBackend())
    frames = {key: canvas for key, canvas in all_frames.items() if len(key) == 3}
    ctx = RuleContext(
        doc=doc,
        palette=resolve_palette(doc.palette),
        frames=frames,
        resolved=resolve_frames(doc),
        tiles={},
    )
    return run_validation(ctx)


# --- swap_palette ------------------------------------------------------------------


def test_swap_palette_remaps_colours_and_round_trips():
    doc = make_doc()
    op = OperationSpec(name="swap_palette", params={"palette_id": "rpg_fantasy", "remap": True})
    new_doc, inverse = apply_operation(doc, op)
    assert new_doc.palette.id == "rpg_fantasy"
    # every shape reference landed on a colour the new palette declares
    refs = {shape.color for region in new_doc.regions.values() for shape in region.shapes}
    assert refs <= set(new_doc.palette.by_id)
    # the doc's own ids (red/blue/green/black) are not in rpg_fantasy, so the
    # hue-aware remap must have moved the torso's colour somewhere
    assert new_doc.regions["torso"].shapes[0].color != "red"
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


def test_swap_palette_own_palette_is_a_reversible_noop():
    doc = make_doc()
    op = OperationSpec(name="swap_palette", params={"palette_id": "hero_palette", "remap": True})
    new_doc, inverse = apply_operation(doc, op)
    assert content_hash(new_doc) == content_hash(doc)
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


def test_swap_palette_remap_false_rejects_missing_colours():
    doc = make_doc()
    op = OperationSpec(name="swap_palette", params={"palette_id": "rpg_fantasy", "remap": False})
    with pytest.raises(OperationError, match="remap=false"):
        apply_operation(doc, op)


def test_swap_palette_respects_op_protect():
    doc = make_doc()
    op = OperationSpec(
        name="swap_palette",
        params={"palette_id": "rpg_fantasy", "remap": True},
        protect=["torso"],
    )
    with pytest.raises(OperationError, match="protected region 'torso'"):
        apply_operation(doc, op)


def test_swap_palette_protected_region_not_recolored():
    # the doc's `shield` region is protected:true — swap_palette must not
    # remap its colours (the W3-B defect: shield black -> stone_shadow).
    # Instead its colour ids are carried into the swapped palette verbatim,
    # so the shield's rendered pixels are byte-identical before and after.
    doc = make_doc()
    op = OperationSpec(name="swap_palette", params={"palette_id": "rpg_fantasy", "remap": True})
    new_doc, inverse = apply_operation(doc, op)
    assert new_doc.regions["shield"].shapes[0].color == "black"
    assert new_doc.palette.by_id["black"].hex == "#222222"
    # unprotected regions still remap onto the new palette
    assert new_doc.regions["torso"].shapes[0].color != "red"
    assert new_doc.regions["torso"].shapes[0].color in new_doc.palette.by_id
    # rendered shield pixel unchanged (shield rect at (2,2) size (4,4), root (16,16))
    before = render_asset_frames(doc, LocalRenderBackend())
    after = render_asset_frames(new_doc, LocalRenderBackend())
    key = next(k for k in before if len(k) == 3)
    assert after[key].get_pixel(18, 18) == before[key].get_pixel(18, 18)
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


# --- apply_material -----------------------------------------------------------------


def test_apply_material_remaps_region_colours_and_round_trips():
    doc = make_material_doc()
    op = OperationSpec(name="apply_material", params={"material": "rusty_iron", "region": "torso"})
    new_doc, inverse = apply_operation(doc, op)
    assert new_doc.regions["torso"].shapes[0].color == "rust"
    assert new_doc.regions["head"].shapes[0].color == "other_a"  # untouched region
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


def test_apply_material_all_regions_skips_protected_and_round_trips():
    doc = make_material_doc()
    data = doc.model_dump(mode="json")
    data["regions"]["shield"] = {
        "anchor": "root",
        "layer": 5,
        "shapes": [{"op": "rect", "color": "other_b", "at": [2, 2], "size": [4, 4]}],
        "protected": True,
    }
    doc = CharacterAsset.model_validate(data)
    op = OperationSpec(name="apply_material", params={"material": "rusty_iron", "region": None})
    new_doc, inverse = apply_operation(doc, op)
    assert new_doc.regions["torso"].shapes[0].color == "rust"  # remapped
    assert new_doc.regions["shield"].shapes[0].color == "other_b"  # protected untouched
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


def test_apply_material_unknown_material_raises():
    doc = make_material_doc()
    op = OperationSpec(name="apply_material", params={"material": "obsidian", "region": "torso"})
    with pytest.raises(OperationError, match="unknown material"):
        apply_operation(doc, op)


def test_apply_material_protected_region_raises():
    doc = make_doc()
    op = OperationSpec(name="apply_material", params={"material": "rusty_iron", "region": "shield"})
    with pytest.raises(OperationError, match="protected"):
        apply_operation(doc, op)


# --- add_component -----------------------------------------------------------------


def test_add_component_inserts_regions_and_palette_and_round_trips():
    doc = make_doc()
    op = OperationSpec(
        name="add_component", params={"component": "backpack_simple", "anchor": "root"}
    )
    new_doc, inverse = apply_operation(doc, op)
    assert "backpack" in new_doc.regions
    assert new_doc.regions["backpack"].anchor == "root"
    # the component references leather ramp + gold ids; `outline` already exists
    added = {c.id for c in new_doc.palette.colors} - {c.id for c in doc.palette.colors}
    assert added == {"leather_shadow", "leather_mid", "leather_light", "accent_gold"}
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


def test_add_component_creates_new_anchor_and_round_trips():
    doc = make_doc()
    op = OperationSpec(
        name="add_component",
        params={"component": "helmet_round", "anchor": "helm", "anchor_at": [16, 12]},
    )
    new_doc, inverse = apply_operation(doc, op)
    assert new_doc.anchors["helm"] == (16, 12)
    assert new_doc.regions["helmet"].anchor == "helm"
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


def test_add_component_anchor_outside_canvas_raises():
    doc = make_doc()
    op = OperationSpec(
        name="add_component",
        params={"component": "helmet_round", "anchor": "helm", "anchor_at": [40, 16]},
    )
    with pytest.raises(OperationError, match="outside the canvas"):
        apply_operation(doc, op)


def test_add_component_rendering_outside_canvas_raises():
    # the helmet's dome rises above its attach point; anchored at y=4 the dome
    # would render off the top of the 32px canvas — the placement check
    # rejects it instead of silently clipping the helmet
    doc = make_doc()
    op = OperationSpec(
        name="add_component",
        params={"component": "helmet_round", "anchor": "helm", "anchor_at": [16, 4]},
    )
    with pytest.raises(OperationError, match="outside the canvas"):
        apply_operation(doc, op)


def test_add_component_offset_shifts_geometry_and_round_trips():
    doc = make_doc()
    op = OperationSpec(
        name="add_component",
        params={"component": "sword_basic", "anchor": "root", "offset": [2, 1]},
    )
    new_doc, inverse = apply_operation(doc, op)
    # sword blade's first rect is at (-1, -12); shifted by (2, 1)
    assert new_doc.regions["sword_blade"].shapes[0].at == (1, -11)
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


def test_add_component_unknown_anchor_raises():
    doc = make_doc()
    op = OperationSpec(
        name="add_component", params={"component": "backpack_simple", "anchor": "nope"}
    )
    with pytest.raises(OperationError, match="unknown anchor"):
        apply_operation(doc, op)


def test_add_component_region_name_collision_raises():
    # shield_round declares a region named `shield`, which already exists in the
    # doc (and is protected) — insertion must refuse rather than clobber it.
    doc = make_doc()
    op = OperationSpec(name="add_component", params={"component": "shield_round", "anchor": "root"})
    with pytest.raises(OperationError, match="already exist"):
        apply_operation(doc, op)


def test_add_component_does_not_disturb_protected_regions():
    doc = make_doc()
    op = OperationSpec(
        name="add_component",
        params={"component": "backpack_simple", "anchor": "root"},
        protect=["torso"],
    )
    new_doc, _ = apply_operation(doc, op)  # must not raise
    assert new_doc.regions["torso"].shapes[0].color == "red"


# --- replace_component ---------------------------------------------------------------


def test_replace_component_swaps_regions_strips_transforms_and_round_trips():
    doc = make_doc()
    data = doc.model_dump(mode="json")
    data["animations"]["idle"]["frames"][0]["transforms"] = {"arm_right": {"offset": [1, 0]}}
    doc = CharacterAsset.model_validate(data)
    op = OperationSpec(
        name="replace_component",
        params={"component": "sword_basic", "anchor": "root", "replace": ["arm_right"]},
    )
    new_doc, inverse = apply_operation(doc, op)
    assert "arm_right" not in new_doc.regions
    assert set(new_doc.regions) == {
        "torso",
        "head",
        "arm_left",
        "shield",
        "sword_blade",
        "sword_guard",
    }
    # the resolver raises on frame transforms referencing unknown regions, so
    # replace_component must have stripped arm_right's per-frame transform
    assert new_doc.animations["idle"].frames[0].transforms == {}
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


def test_replace_component_default_replace_reequips_and_round_trips():
    doc = make_doc()
    doc, _ = apply_operation(
        doc,
        OperationSpec(name="add_component", params={"component": "sword_basic", "anchor": "root"}),
    )
    # no `replace` param: re-equip the component's own regions already in the doc
    op = OperationSpec(
        name="replace_component", params={"component": "sword_basic", "anchor": "root"}
    )
    new_doc, inverse = apply_operation(doc, op)
    assert "sword_blade" in new_doc.regions
    assert "sword_guard" in new_doc.regions
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


def test_replace_component_protected_region_raises():
    doc = make_doc()
    op = OperationSpec(
        name="replace_component",
        params={"component": "sword_basic", "anchor": "root", "replace": ["shield"]},
    )
    with pytest.raises(OperationError, match="protected"):
        apply_operation(doc, op)


def test_replace_component_unknown_region_raises():
    doc = make_doc()
    op = OperationSpec(
        name="replace_component",
        params={"component": "sword_basic", "anchor": "root", "replace": ["nope"]},
    )
    with pytest.raises(OperationError, match="unknown region"):
        apply_operation(doc, op)


# --- change_pose --------------------------------------------------------------------


def test_change_pose_writes_template_transforms_and_round_trips():
    doc = make_doc()
    op = OperationSpec(name="change_pose", params={"animation": "idle", "pose": "idle"})
    new_doc, inverse = apply_operation(doc, op)
    f0 = new_doc.animations["idle"].frames[0].transforms
    f1 = new_doc.animations["idle"].frames[1].transforms
    # idle template: even frames bob down 1px, arms sway counter-phase
    assert f0["head"].offset == (0, -1)
    assert f0["arm_left"].offset == (1, -1)
    assert f0["arm_right"].offset == (-1, -1)
    assert f1["head"].offset == (0, 0)
    assert f1["arm_left"].offset == (-1, 0)
    assert f1["arm_right"].offset == (1, 0)
    assert "torso" not in f0  # no body-named region; torso is not driven
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


def test_change_pose_targets_subset_of_frames():
    doc = make_doc()
    op = OperationSpec(
        name="change_pose",
        params={"animation": "idle", "pose": "attack_anticipation", "frames": [0]},
    )
    new_doc, inverse = apply_operation(doc, op)
    assert new_doc.animations["idle"].frames[0].transforms["arm_left"].offset == (0, -1)
    assert new_doc.animations["idle"].frames[1].transforms == {}
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


def test_change_pose_unknown_pose_raises():
    doc = make_doc()
    op = OperationSpec(name="change_pose", params={"animation": "idle", "pose": "moonwalk"})
    with pytest.raises(OperationError, match="unknown pose"):
        apply_operation(doc, op)


def test_change_pose_unknown_animation_raises():
    doc = make_doc()
    op = OperationSpec(name="change_pose", params={"animation": "nope", "pose": "idle"})
    with pytest.raises(OperationError, match="unknown animation"):
        apply_operation(doc, op)


def test_change_pose_does_not_disturb_protected_region_shapes():
    # change_pose only writes frame transforms; check_protection compares region
    # shapes, so protecting a region must not false-positive.
    doc = make_doc()
    op = OperationSpec(
        name="change_pose",
        params={"animation": "idle", "pose": "idle"},
        protect=["torso"],
    )
    new_doc, _ = apply_operation(doc, op)  # must not raise
    assert new_doc.regions["torso"].shapes[0].color == "red"


def test_change_pose_excludes_protected_role_regions():
    # a protected region named `head` must not be driven by pose templates —
    # role discovery treats protected regions as static (the W3-B gap)
    doc = make_doc()
    data = doc.model_dump(mode="json")
    data["regions"]["head"]["protected"] = True
    doc = CharacterAsset.model_validate(data)
    op = OperationSpec(name="change_pose", params={"animation": "idle", "pose": "idle"})
    new_doc, inverse = apply_operation(doc, op)
    f0 = new_doc.animations["idle"].frames[0].transforms
    f1 = new_doc.animations["idle"].frames[1].transforms
    assert "head" not in f0 and "head" not in f1  # protected head not animated
    assert f0["arm_left"].offset == (1, -1)  # unprotected roles still move
    assert f1["arm_right"].offset == (1, 0)
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


# --- repair_outline ----------------------------------------------------------------


def test_repair_outline_fills_gap_and_round_trips():
    doc = make_bitmap_doc()
    op = OperationSpec(name="repair_outline", params={"region": "art"})
    new_doc, inverse = apply_operation(doc, op)
    bitmap = new_doc.regions["art"].shapes[0]
    # The ring-path gap cell (middle row, right column) closes; the interior
    # fill cell (middle row, middle column) is preserved — repair must never
    # destroy legitimate interior fill (the R2 critic's over-fill defect).
    assert bitmap.rows == ["ooo", "oxo", "ooo"]
    # `x` is still referenced by the interior cell, so the key is untouched.
    assert bitmap.key == {"o": "outline", "x": "red"}
    # RENDERED ring closure: the ring-border gap cell (middle row, right
    # column) is red before repair and outline-coloured after — the W3-B
    # defect was that this cell stayed red while the interior flipped
    palette = resolve_palette(doc.palette)
    before = render_asset_frames(doc, LocalRenderBackend())
    after = render_asset_frames(new_doc, LocalRenderBackend())
    key = next(k for k in before if len(k) == 3)
    gap_cell = (16 + 2, 16 + 1)  # rows[1][2]; bitmap at (0,0), root anchor (16,16)
    assert before[key].get_pixel(*gap_cell) == palette.rgba("red")  # ring open
    assert after[key].get_pixel(*gap_cell) == palette.rgba("outline")  # ring closed
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


def test_repair_outline_fills_transparent_hole_and_round_trips():
    # a transparent hole (`.`) flanked by outline cells above and below must
    # also be filled with the outline colour
    doc = make_doc()
    data = doc.model_dump(mode="json")
    data["regions"]["art"] = {
        "anchor": "root",
        "layer": 5,
        "shapes": [
            {
                "op": "bitmap",
                "at": [0, 0],
                "key": {"o": "outline"},
                "rows": ["ooo", "o.o", "ooo"],
            }
        ],
    }
    doc = CharacterAsset.model_validate(data)
    op = OperationSpec(name="repair_outline", params={"region": "art"})
    new_doc, inverse = apply_operation(doc, op)
    assert new_doc.regions["art"].shapes[0].rows == ["ooo", "ooo", "ooo"]
    palette = resolve_palette(doc.palette)
    before = render_asset_frames(doc, LocalRenderBackend())
    after = render_asset_frames(new_doc, LocalRenderBackend())
    key = next(k for k in before if len(k) == 3)
    hole_cell = (16 + 1, 16 + 1)  # rows[1][1] — the transparent hole
    assert before[key].get_pixel(*hole_cell) == palette.rgba("red")  # torso shows through
    assert after[key].get_pixel(*hole_cell) == palette.rgba("outline")  # hole filled
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


def test_repair_outline_all_regions_round_trips():
    doc = make_bitmap_doc()
    op = OperationSpec(name="repair_outline", params={"region": None})
    new_doc, inverse = apply_operation(doc, op)
    # Ring-path gap closes, interior fill preserved (same as the region-scoped
    # test — the region=None path must behave identically).
    assert new_doc.regions["art"].shapes[0].rows == ["ooo", "oxo", "ooo"]
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


def test_repair_outline_skips_bitmaps_without_outline_key():
    doc = make_doc()
    data = doc.model_dump(mode="json")
    data["regions"]["art"] = {
        "anchor": "root",
        "layer": 5,
        "shapes": [{"op": "bitmap", "at": [0, 0], "key": {"x": "red"}, "rows": ["xxx"]}],
    }
    doc = CharacterAsset.model_validate(data)
    op = OperationSpec(name="repair_outline", params={"region": "art"})
    new_doc, inverse = apply_operation(doc, op)
    assert new_doc.regions["art"].shapes[0].rows == ["xxx"]
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


@pytest.mark.parametrize(
    ("rows", "key", "label"),
    [
        (["ooo", "oxo", "ooo"], {"o": "outline", "x": "red"}, "3x3 closed ring"),
        (
            ["oooo", "oxxo", "oxxo", "oooo"],
            {"o": "outline", "x": "red"},
            "4x4 closed ring with 2x2 interior",
        ),
        (
            ["oooo", "oxoo", "ooox", "oooo"],
            {"o": "outline", "x": "red"},
            "concave-corner interior cells",
        ),
        (
            ["oooooo", "ooxxoo", "ooxxoo", "oooooo"],
            {"o": "outline", "x": "red"},
            "1x2 interior",
        ),
        (
            ["ooooooo", "ooxxxoo", "ooxxxoo", "ooooooo"],
            {"o": "outline", "x": "red"},
            "multi-pixel interior",
        ),
        (["oooo", "o..o", "o..o", "oooo"], {"o": "outline"}, "4x4 donut hollow (R3 critic)"),
        (
            ["ooooooo", "oo...oo", "oo...oo", "ooooooo"],
            {"o": "outline"},
            "7x7 donut hollow",
        ),
        (
            ["oooooo", "o....o", "o....o", "oooooo"],
            {"o": "outline"},
            "1x4 transparent interior",
        ),
    ],
)
def test_repair_outline_is_noop_on_closed_rings(
    rows: list[str], key: dict[str, str], label: str
) -> None:
    # The R2/R3 critics' over-fill defect: repair must be a NO-OP on a healthy
    # closed ring (any interior fill, concave corners, multi-pixel interiors,
    # and transparent donut hollows). Only a genuine ring-path gap (a break in
    # the outline ring itself) may be filled — interior cells are legitimate
    # art, never repair targets.
    doc = make_doc()
    data = doc.model_dump(mode="json")
    data["regions"]["art"] = {
        "anchor": "root",
        "layer": 5,
        "shapes": [{"op": "bitmap", "at": [0, 0], "key": key, "rows": rows}],
    }
    doc = CharacterAsset.model_validate(data)
    op = OperationSpec(name="repair_outline", params={"region": "art"})
    new_doc, inverse = apply_operation(doc, op)
    assert new_doc.regions["art"].shapes[0].rows == rows, label
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc), label


def test_repair_outline_protected_region_raises():
    doc = make_bitmap_doc()
    op = OperationSpec(name="repair_outline", params={"region": "shield"})
    with pytest.raises(OperationError, match="protected"):
        apply_operation(doc, op)


# --- end-to-end: the W3-B demo recipe validates zero-blocking ------------------------


def test_semantic_stack_renders_and_validates_zero_blocking():
    # The W3-B demo recipe: add a component first (grows the palette), swap to a
    # curated palette, then drive an idle pose — the result must still render
    # and validate with zero blocking findings.
    doc = make_doc()
    # The recipe legitimately exceeds the 24-colour default: swap_palette mounts
    # rpg_fantasy (24) and carries the protected shield's `black` colour over
    # verbatim (protected refs are never remapped) -> 25. Declare the limit the
    # recipe needs; this mirrors how a real agent would configure a doc it plans
    # to grow with components.
    doc.validation.palette_limit = 32
    doc, _ = apply_operation(
        doc,
        OperationSpec(
            name="add_component", params={"component": "backpack_simple", "anchor": "root"}
        ),
    )
    doc, _ = apply_operation(
        doc, OperationSpec(name="swap_palette", params={"palette_id": "rpg_fantasy", "remap": True})
    )
    doc, _ = apply_operation(
        doc, OperationSpec(name="change_pose", params={"animation": "idle", "pose": "idle"})
    )
    frames = render_asset_frames(doc, LocalRenderBackend())
    assert frames  # renders non-empty
    report = _validate(doc)
    assert report.blocking is False
    assert report.error_count == 0
