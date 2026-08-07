"""Tests for revisions/operations.py and revisions/store.py (Task 10)."""

from __future__ import annotations

import pytest

from pixel_forge.domain.hashing import content_hash
from pixel_forge.domain.paths import ProjectPaths
from pixel_forge.errors import ForgeError, OperationError
from pixel_forge.revisions.operations import (
    affected_targets,
    apply_operation,
    available_operations,
    check_protection,
)
from pixel_forge.revisions.store import (
    compare_revisions,
    head_revision,
    load_revisions,
    record_revision,
    revert_revision,
)
from pixel_forge.schemas.animation import AnimationSpec, FrameSpec
from pixel_forge.schemas.asset import AssetHeader, CharacterAsset, ExportOptions, ValidationOptions
from pixel_forge.schemas.common import EllipseShape, LineShape, PixelShape, RectShape, Region
from pixel_forge.schemas.palette import Palette, PaletteColor
from pixel_forge.schemas.project import ProjectConfig
from pixel_forge.schemas.revision import OperationSpec


def make_doc() -> CharacterAsset:
    return CharacterAsset(
        schema_version=1,
        asset=AssetHeader(id="hero", type="character", canvas=(32, 32)),
        palette=Palette(
            id="pal",
            colors=[
                PaletteColor(id="red", hex="#ff0000"),
                PaletteColor(id="blue", hex="#0000ff"),
                PaletteColor(id="green", hex="#00ff00"),
                PaletteColor(id="black", hex="#000000"),
            ],
        ),
        export=ExportOptions(),
        validation=ValidationOptions(),
        directions=["down", "up"],
        anchors={"root": (0, 0), "head_anchor": (10, 10)},
        regions={
            "body": Region(
                anchor="root",
                layer=0,
                shapes=[
                    RectShape(op="rect", color="red", at=(2, 2), size=(8, 8)),
                    PixelShape(op="pixel", color="black", at=(0, 0)),
                    LineShape(op="line", color="black", start=(0, 0), end=(4, 4)),
                ],
            ),
            "head": Region(
                anchor="head_anchor",
                layer=1,
                shapes=[EllipseShape(op="ellipse", color="blue", at=(1, 1), size=(6, 6))],
            ),
            "shield": Region(
                anchor="root",
                layer=2,
                shapes=[RectShape(op="rect", color="green", at=(0, 0), size=(4, 4))],
                protected=True,
            ),
        },
        animations={
            "idle": AnimationSpec(frames=[FrameSpec(duration_ms=100), FrameSpec(duration_ms=150)]),
        },
    )


def make_doc_with_bitmap() -> CharacterAsset:
    """`make_doc()` plus two extra regions carrying `bitmap` shapes.

    `ink` is bitmap-only (recolor/translate/resize-bitmap-only tests); `mixed`
    combines a rect with a bitmap (resize-mixed test). Kept separate from
    `make_doc()` so existing tests' shape-index assumptions (e.g. `shapes[0]`)
    stay untouched.
    """
    doc = make_doc()
    data = doc.model_dump(mode="json")
    data["regions"]["ink"] = {
        "anchor": "root",
        "layer": 3,
        "shapes": [
            {"op": "bitmap", "at": [0, 0], "key": {"o": "black", "r": "red"}, "rows": ["or"]}
        ],
    }
    data["regions"]["mixed"] = {
        "anchor": "root",
        "layer": 4,
        "shapes": [
            {"op": "rect", "color": "blue", "at": [0, 0], "size": [4, 4]},
            {"op": "bitmap", "at": [1, 1], "key": {"g": "green"}, "rows": ["g"]},
        ],
    }
    return CharacterAsset.model_validate(data)


@pytest.fixture
def paths(tmp_path):
    return ProjectPaths(root=tmp_path, config=ProjectConfig(name="test"))


# --- resize_region ------------------------------------------------------------


def test_resize_region_round_trip():
    doc = make_doc()
    op = OperationSpec(
        name="resize_region", params={"region": "body", "delta": [2, 2], "shape_indices": [0]}
    )
    new_doc, inverse = apply_operation(doc, op)
    shape = new_doc.regions["body"].shapes[0]
    assert shape.size == (10, 10)
    assert shape.at == (1, 1)
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


def test_resize_region_centre_preservation_even_delta():
    doc = make_doc()
    op = OperationSpec(
        name="resize_region", params={"region": "body", "delta": [4, 4], "shape_indices": [0]}
    )
    new_doc, _ = apply_operation(doc, op)
    shape = new_doc.regions["body"].shapes[0]
    assert shape.size == (12, 12)
    assert shape.at == (0, 0)


def test_resize_region_centre_preservation_odd_delta():
    doc = make_doc()
    op = OperationSpec(
        name="resize_region", params={"region": "body", "delta": [3, 3], "shape_indices": [0]}
    )
    new_doc, inverse = apply_operation(doc, op)
    shape = new_doc.regions["body"].shapes[0]
    assert shape.size == (11, 11)
    assert shape.at == (1, 1)
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


def test_resize_region_ignores_pixel_and_line_shapes():
    doc = make_doc()
    op = OperationSpec(name="resize_region", params={"region": "body", "delta": [2, 2]})
    new_doc, _ = apply_operation(doc, op)
    assert new_doc.regions["body"].shapes[1].at == (0, 0)  # pixel untouched
    assert new_doc.regions["body"].shapes[2].start == (0, 0)  # line untouched


def test_resize_region_below_min_size_raises():
    doc = make_doc()
    op = OperationSpec(
        name="resize_region", params={"region": "body", "delta": [-10, -10], "shape_indices": [0]}
    )
    with pytest.raises(OperationError):
        apply_operation(doc, op)


def test_resize_region_bitmap_only_region_raises():
    doc = make_doc_with_bitmap()
    op = OperationSpec(name="resize_region", params={"region": "ink", "delta": [2, 2]})
    with pytest.raises(OperationError, match="bitmap"):
        apply_operation(doc, op)


def test_resize_region_mixed_region_resizes_rect_leaves_bitmap():
    doc = make_doc_with_bitmap()
    op = OperationSpec(name="resize_region", params={"region": "mixed", "delta": [2, 2]})
    new_doc, _ = apply_operation(doc, op)
    rect, bitmap = new_doc.regions["mixed"].shapes
    assert rect.size == (6, 6)
    assert rect.at == (-1, -1)
    assert bitmap.at == (1, 1)
    assert bitmap.rows == ["g"]


# --- translate_region -----------------------------------------------------------


def test_translate_region_round_trip():
    doc = make_doc()
    op = OperationSpec(name="translate_region", params={"region": "head", "offset": [3, -2]})
    new_doc, inverse = apply_operation(doc, op)
    assert new_doc.regions["head"].shapes[0].at == (4, -1)
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


def test_translate_region_bitmap_moves_at_and_round_trips():
    doc = make_doc_with_bitmap()
    op = OperationSpec(name="translate_region", params={"region": "ink", "offset": [5, -3]})
    new_doc, inverse = apply_operation(doc, op)
    assert new_doc.regions["ink"].shapes[0].at == (5, -3)
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


# --- recolor_region ---------------------------------------------------------------


def test_recolor_region_round_trip():
    doc = make_doc()
    op = OperationSpec(
        name="recolor_region",
        params={"region": "body", "mapping": {"red": "blue", "black": "green"}},
    )
    new_doc, inverse = apply_operation(doc, op)
    assert new_doc.regions["body"].shapes[0].color == "blue"
    assert new_doc.regions["body"].shapes[1].color == "green"
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


def test_recolor_region_unknown_target_raises():
    doc = make_doc()
    op = OperationSpec(
        name="recolor_region", params={"region": "body", "mapping": {"red": "purple"}}
    )
    with pytest.raises(OperationError):
        apply_operation(doc, op)


def test_recolor_region_non_injective_mapping_raises():
    doc = make_doc()
    op = OperationSpec(
        name="recolor_region",
        params={"region": "body", "mapping": {"red": "blue", "black": "blue"}},
    )
    with pytest.raises(OperationError):
        apply_operation(doc, op)


def test_recolor_region_bitmap_does_not_raise_keyerror():
    # Confirmed bug: _recolor_region did `shape_data["color"]` unconditionally, which
    # raises KeyError on a bitmap shape (no `color` field; colour lives in `key`'s
    # values). Reproduced by reverting the fix and confirming this test then fails
    # with KeyError instead of passing.
    doc = make_doc_with_bitmap()
    op = OperationSpec(
        name="recolor_region", params={"region": "ink", "mapping": {"black": "green"}}
    )
    apply_operation(doc, op)  # must not raise KeyError


def test_recolor_region_bitmap_rewrites_key_and_round_trips():
    doc = make_doc_with_bitmap()
    op = OperationSpec(
        name="recolor_region",
        params={"region": "ink", "mapping": {"black": "green", "red": "blue"}},
    )
    new_doc, inverse = apply_operation(doc, op)
    bitmap = new_doc.regions["ink"].shapes[0]
    assert bitmap.key == {"o": "green", "r": "blue"}
    assert bitmap.rows == ["or"]
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


# --- set_frame_duration -----------------------------------------------------------


def test_set_frame_duration_single_frame_round_trip():
    doc = make_doc()
    op = OperationSpec(
        name="set_frame_duration", params={"animation": "idle", "frame": 0, "duration_ms": 250}
    )
    new_doc, inverse = apply_operation(doc, op)
    assert new_doc.animations["idle"].frames[0].duration_ms == 250
    assert new_doc.animations["idle"].frames[1].duration_ms == 150
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


def test_set_frame_duration_all_frames_round_trip():
    doc = make_doc()
    op = OperationSpec(
        name="set_frame_duration", params={"animation": "idle", "frame": None, "duration_ms": 200}
    )
    new_doc, inverse = apply_operation(doc, op)
    assert [f.duration_ms for f in new_doc.animations["idle"].frames] == [200, 200]
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


def test_set_frame_duration_round_trips_with_bitmap_region_present():
    # set_frame_duration never touches shapes; this proves a doc that happens to
    # carry a bitmap region still serialises/re-validates cleanly through the
    # dump-mutate-revalidate cycle every operation goes through.
    doc = make_doc_with_bitmap()
    op = OperationSpec(
        name="set_frame_duration", params={"animation": "idle", "frame": 0, "duration_ms": 250}
    )
    new_doc, inverse = apply_operation(doc, op)
    assert new_doc.animations["idle"].frames[0].duration_ms == 250
    assert new_doc.regions["ink"].shapes[0].key == {"o": "black", "r": "red"}
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


# --- add_frame / remove_frame -------------------------------------------------------


def test_add_frame_round_trip():
    doc = make_doc()
    op = OperationSpec(
        name="add_frame",
        params={"animation": "idle", "at": 1, "frame": {"duration_ms": 80, "events": ["step"]}},
    )
    new_doc, inverse = apply_operation(doc, op)
    assert len(new_doc.animations["idle"].frames) == 3
    assert new_doc.animations["idle"].frames[1].duration_ms == 80
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


def test_remove_frame_round_trip():
    doc = make_doc()
    op = OperationSpec(name="remove_frame", params={"animation": "idle", "at": 0})
    new_doc, inverse = apply_operation(doc, op)
    assert len(new_doc.animations["idle"].frames) == 1
    assert new_doc.animations["idle"].frames[0].duration_ms == 150
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


def test_remove_frame_last_remaining_raises():
    doc = make_doc()
    new_doc, _ = apply_operation(
        doc, OperationSpec(name="remove_frame", params={"animation": "idle", "at": 0})
    )
    with pytest.raises(OperationError):
        apply_operation(
            new_doc, OperationSpec(name="remove_frame", params={"animation": "idle", "at": 0})
        )


def test_add_frame_round_trips_with_bitmap_region_present():
    doc = make_doc_with_bitmap()
    op = OperationSpec(
        name="add_frame", params={"animation": "idle", "at": 1, "frame": {"duration_ms": 80}}
    )
    new_doc, inverse = apply_operation(doc, op)
    assert len(new_doc.animations["idle"].frames) == 3
    assert new_doc.regions["mixed"].shapes[1].key == {"g": "green"}
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


def test_remove_frame_round_trips_with_bitmap_region_present():
    doc = make_doc_with_bitmap()
    op = OperationSpec(name="remove_frame", params={"animation": "idle", "at": 0})
    new_doc, inverse = apply_operation(doc, op)
    assert len(new_doc.animations["idle"].frames) == 1
    assert new_doc.regions["ink"].shapes[0].rows == ["or"]
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


# --- set_region_visibility --------------------------------------------------------------


def test_set_region_visibility_frames_round_trip():
    doc = make_doc()
    op = OperationSpec(
        name="set_region_visibility",
        params={"region": "head", "visible": False, "animation": "idle", "frames": [0, 1]},
    )
    new_doc, inverse = apply_operation(doc, op)
    assert new_doc.animations["idle"].frames[0].transforms["head"].visible is False
    assert new_doc.animations["idle"].frames[1].transforms["head"].visible is False
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


def test_set_region_visibility_directions_round_trip():
    doc = make_doc()
    op = OperationSpec(
        name="set_region_visibility",
        params={"region": "head", "visible": False, "directions": ["up"]},
    )
    new_doc, inverse = apply_operation(doc, op)
    assert new_doc.direction_overrides["up"]["head"].visible is False
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


def test_set_region_visibility_requires_frames_or_directions():
    doc = make_doc()
    op = OperationSpec(name="set_region_visibility", params={"region": "head", "visible": False})
    with pytest.raises(OperationError):
        apply_operation(doc, op)


def test_set_region_visibility_round_trips_on_bitmap_region():
    doc = make_doc_with_bitmap()
    op = OperationSpec(
        name="set_region_visibility",
        params={"region": "ink", "visible": False, "directions": ["up"]},
    )
    new_doc, inverse = apply_operation(doc, op)
    assert new_doc.direction_overrides["up"]["ink"].visible is False
    assert new_doc.regions["ink"].shapes[0].key == {"o": "black", "r": "red"}
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


# --- protection rules ----------------------------------------------------------------


def test_protected_region_cannot_be_modified():
    doc = make_doc()
    op = OperationSpec(name="translate_region", params={"region": "shield", "offset": [1, 1]})
    with pytest.raises(OperationError):
        apply_operation(doc, op)


def test_op_protect_region_violation_raises():
    doc = make_doc()
    op = OperationSpec(
        name="translate_region", params={"region": "body", "offset": [1, 1]}, protect=["body"]
    )
    with pytest.raises(OperationError):
        apply_operation(doc, op)


def test_check_protection_anchor_violation_raises():
    doc = make_doc()
    data = doc.model_dump(mode="json")
    data["anchors"]["root"] = [99, 99]
    mutated = CharacterAsset.model_validate(data)
    with pytest.raises(OperationError):
        check_protection(doc, mutated, ["root"])


def test_check_protection_no_violation_passes():
    doc = make_doc()
    check_protection(doc, doc, ["root", "body"])  # nothing changed, must not raise


def test_check_protection_unknown_name_raises():
    doc = make_doc()
    with pytest.raises(OperationError):
        check_protection(doc, doc, ["not_a_real_anchor_or_region"])


# --- replace_spec -------------------------------------------------------------------------


def test_replace_spec_round_trip():
    doc = make_doc()
    new_spec = doc.model_dump(mode="json")
    new_spec["directions"] = ["down", "up", "left"]
    op = OperationSpec(name="replace_spec", params={"spec": new_spec})
    new_doc, inverse = apply_operation(doc, op)
    assert new_doc.directions == ["down", "up", "left"]
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


def test_replace_spec_rejects_asset_id_change():
    doc = make_doc()
    new_spec = doc.model_dump(mode="json")
    new_spec["asset"]["id"] = "villain"
    op = OperationSpec(name="replace_spec", params={"spec": new_spec})
    with pytest.raises(OperationError):
        apply_operation(doc, op)


def test_replace_spec_rejects_protected_region_change():
    doc = make_doc()
    new_spec = doc.model_dump(mode="json")
    new_spec["regions"]["shield"]["shapes"][0]["size"] = [10, 10]
    op = OperationSpec(name="replace_spec", params={"spec": new_spec})
    with pytest.raises(OperationError):
        apply_operation(doc, op)


def test_replace_spec_honours_op_protect():
    doc = make_doc()
    new_spec = doc.model_dump(mode="json")
    new_spec["regions"]["body"]["shapes"][0]["size"] = [20, 20]
    op = OperationSpec(name="replace_spec", params={"spec": new_spec}, protect=["body"])
    with pytest.raises(OperationError):
        apply_operation(doc, op)


def test_replace_spec_rejects_invalid_schema():
    doc = make_doc()
    new_spec = doc.model_dump(mode="json")
    del new_spec["palette"]
    op = OperationSpec(name="replace_spec", params={"spec": new_spec})
    with pytest.raises(OperationError):
        apply_operation(doc, op)


def test_replace_spec_round_trips_bitmap_shapes():
    doc = make_doc_with_bitmap()
    new_spec = doc.model_dump(mode="json")
    new_spec["directions"] = ["down", "up", "left"]
    op = OperationSpec(name="replace_spec", params={"spec": new_spec})
    new_doc, inverse = apply_operation(doc, op)
    assert new_doc.directions == ["down", "up", "left"]
    assert new_doc.regions["ink"].shapes[0].key == {"o": "black", "r": "red"}
    assert new_doc.regions["mixed"].shapes[1].rows == ["g"]
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)


def test_replace_spec_revision_is_revertible(paths):
    # Reproduces the bug this operation fixes: a `replace_spec` revision used to
    # record itself as both operation and inverse without registering a handler for
    # that name, so `revert_revision` raised `OperationError: unknown operation
    # 'replace_spec'`.
    doc = make_doc()
    new_spec = doc.model_dump(mode="json")
    new_spec["directions"] = ["down", "up", "left"]
    op = OperationSpec(name="replace_spec", params={"spec": new_spec})
    doc1, inv = apply_operation(doc, op)
    rec = record_revision(
        paths, "hero", operation=op, inverse=inv, doc_before=doc, doc_after=doc1, timestamp="t1"
    )
    reverted_doc, _inverse_of_inverse = revert_revision(paths, "hero", rec.revision_id, doc1)
    assert content_hash(reverted_doc) == content_hash(doc)


# --- unknown operation/region/animation/frame ------------------------------------------


def test_unknown_operation_raises():
    doc = make_doc()
    with pytest.raises(OperationError):
        apply_operation(doc, OperationSpec(name="nope", params={}))


def test_unknown_region_raises():
    doc = make_doc()
    op = OperationSpec(name="translate_region", params={"region": "nope", "offset": [1, 1]})
    with pytest.raises(OperationError):
        apply_operation(doc, op)


def test_unknown_animation_raises():
    doc = make_doc()
    op = OperationSpec(
        name="set_frame_duration", params={"animation": "nope", "frame": None, "duration_ms": 1}
    )
    with pytest.raises(OperationError):
        apply_operation(doc, op)


def test_unknown_frame_index_raises():
    doc = make_doc()
    op = OperationSpec(name="remove_frame", params={"animation": "idle", "at": 99})
    with pytest.raises(OperationError):
        apply_operation(doc, op)


# --- registry helpers -------------------------------------------------------------------


def test_available_operations_lists_all_registered():
    names = {info.name for info in available_operations()}
    assert names == {
        # Core low-level revisions.
        "resize_region",
        "translate_region",
        "recolor_region",
        "set_frame_duration",
        "add_frame",
        "remove_frame",
        "set_region_visibility",
        "replace_spec",
        # W3-B semantic agent-facing operations.
        "swap_palette",
        "apply_material",
        "add_component",
        "replace_component",
        "change_pose",
        "repair_outline",
    }


def test_affected_targets_reports_region():
    doc = make_doc()
    op = OperationSpec(name="translate_region", params={"region": "head", "offset": [1, 1]})
    assert affected_targets(doc, op)["regions"] == ["head"]


# --- store.py ---------------------------------------------------------------------------


def test_record_revision_deterministic_id(tmp_path):
    doc = make_doc()
    op = OperationSpec(name="translate_region", params={"region": "head", "offset": [1, 1]})
    new_doc, inverse = apply_operation(doc, op)
    paths_a = ProjectPaths(root=tmp_path / "a", config=ProjectConfig(name="a"))
    paths_b = ProjectPaths(root=tmp_path / "b", config=ProjectConfig(name="b"))
    rec_a = record_revision(
        paths_a,
        "hero",
        operation=op,
        inverse=inverse,
        doc_before=doc,
        doc_after=new_doc,
        timestamp="2026-08-05T00:00:00Z",
    )
    rec_b = record_revision(
        paths_b,
        "hero",
        operation=op,
        inverse=inverse,
        doc_before=doc,
        doc_after=new_doc,
        timestamp="2020-01-01T00:00:00Z",
    )
    assert rec_a.revision_id == rec_b.revision_id


def test_load_revisions_order_and_head(paths):
    doc = make_doc()
    op1 = OperationSpec(name="translate_region", params={"region": "head", "offset": [1, 1]})
    doc1, inv1 = apply_operation(doc, op1)
    record_revision(
        paths, "hero", operation=op1, inverse=inv1, doc_before=doc, doc_after=doc1, timestamp="t1"
    )

    op2 = OperationSpec(name="translate_region", params={"region": "head", "offset": [2, 2]})
    doc2, inv2 = apply_operation(doc1, op2)
    record_revision(
        paths, "hero", operation=op2, inverse=inv2, doc_before=doc1, doc_after=doc2, timestamp="t2"
    )

    revisions = load_revisions(paths, "hero")
    assert [r.timestamp for r in revisions] == ["t1", "t2"]
    assert revisions[1].parent_revision == revisions[0].revision_id
    assert head_revision(paths, "hero").revision_id == revisions[1].revision_id


def test_compare_revisions_changed_regions_and_unknown_id(paths):
    doc = make_doc()
    op1 = OperationSpec(name="translate_region", params={"region": "head", "offset": [1, 1]})
    doc1, inv1 = apply_operation(doc, op1)
    rec1 = record_revision(
        paths, "hero", operation=op1, inverse=inv1, doc_before=doc, doc_after=doc1, timestamp="t1"
    )

    op2 = OperationSpec(name="translate_region", params={"region": "body", "offset": [2, 2]})
    doc2, inv2 = apply_operation(doc1, op2)
    rec2 = record_revision(
        paths, "hero", operation=op2, inverse=inv2, doc_before=doc1, doc_after=doc2, timestamp="t2"
    )

    diff = compare_revisions(paths, "hero", rec1.revision_id, rec2.revision_id)
    assert diff.affected_regions == ["body"]
    assert diff.hash_a == rec1.hash_after
    assert diff.hash_b == rec2.hash_after

    with pytest.raises(ForgeError):
        compare_revisions(paths, "hero", "not-a-real-id", rec2.revision_id)


def test_revert_revision_round_trip(paths):
    doc = make_doc()
    op = OperationSpec(name="translate_region", params={"region": "head", "offset": [3, 3]})
    doc1, inv = apply_operation(doc, op)
    rec = record_revision(
        paths, "hero", operation=op, inverse=inv, doc_before=doc, doc_after=doc1, timestamp="t1"
    )

    reverted_doc, _inverse_of_inverse = revert_revision(paths, "hero", rec.revision_id, doc1)
    assert content_hash(reverted_doc) == content_hash(doc)


def test_revert_revision_unknown_id_raises(paths):
    with pytest.raises(ForgeError):
        revert_revision(paths, "hero", "not-a-real-id", make_doc())
