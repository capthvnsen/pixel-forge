"""Tests for the starter component library (pixel_forge.components).

Every starter component must load and validate, reference only colour ids the
engine can source (the base doc palette or a curated palette), and render clean
(zero blocking validation findings) when `add_component` inserts it into a
minimal character doc — the insertion must also be reversible.
"""

from __future__ import annotations

import pytest

from pixel_forge.animation.resolver import resolve_frames
from pixel_forge.components import available_components, load_component
from pixel_forge.domain.hashing import content_hash
from pixel_forge.domain.palette import resolve_palette
from pixel_forge.errors import OperationError
from pixel_forge.references.curated import CURATED_PALETTES
from pixel_forge.rendering import LocalRenderBackend, render_asset_frames
from pixel_forge.revisions.operations import apply_operation
from pixel_forge.schemas.animation import AnimationSpec, FrameSpec
from pixel_forge.schemas.asset import AssetHeader, CharacterAsset, ExportOptions, ValidationOptions
from pixel_forge.schemas.common import EllipseShape, RectShape, Region
from pixel_forge.schemas.palette import Palette, PaletteColor
from pixel_forge.schemas.revision import OperationSpec
from pixel_forge.schemas.validation import ValidationReport
from pixel_forge.validation.engine import RuleContext, run_validation

COMPONENT_IDS: tuple[str, ...] = ("backpack_simple", "helmet_round", "shield_round", "sword_basic")


def make_character_doc() -> CharacterAsset:
    """A minimal single-direction character doc the components attach to."""
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
        },
        animations={
            "idle": AnimationSpec(
                loop=True,
                frames=[FrameSpec(duration_ms=100), FrameSpec(duration_ms=100)],
            ),
        },
    )


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


# --- library surface --------------------------------------------------------------


def test_components_library_lists_all_starter_ids():
    assert available_components() == COMPONENT_IDS


def test_load_component_unknown_raises():
    with pytest.raises(OperationError, match="unknown component"):
        load_component("not_a_component")


@pytest.mark.parametrize("component_id", COMPONENT_IDS)
def test_component_loads_and_validates(component_id: str):
    spec = load_component(component_id)
    assert spec.id == component_id
    assert spec.regions
    # every region anchors at the placeholder anchor, by convention "@attach",
    # which add_component rewrites to a real anchor at insertion time
    assert all(region.anchor == "@attach" for region in spec.regions.values())


@pytest.mark.parametrize("component_id", COMPONENT_IDS)
def test_component_colours_resolve_from_curated_or_base_palette(component_id: str):
    spec = load_component(component_id)
    base_ids = {c.id for c in make_character_doc().palette.colors}
    curated_ids = {c["id"] for palette in CURATED_PALETTES.values() for c in palette["colors"]}
    assert spec.color_ids <= base_ids | curated_ids


# --- render cleanliness -----------------------------------------------------------


@pytest.mark.parametrize("component_id", COMPONENT_IDS)
def test_component_renders_clean_and_insertion_round_trips(component_id: str):
    doc = make_character_doc()
    new_doc, inverse = apply_operation(
        doc,
        OperationSpec(name="add_component", params={"component": component_id, "anchor": "root"}),
    )
    assert isinstance(new_doc, CharacterAsset)  # add_component only applies to sprite docs
    # renders non-empty frames through the default local backend
    frames = render_asset_frames(new_doc, LocalRenderBackend())
    assert frames

    report = _validate(new_doc)
    assert report.blocking is False
    assert report.error_count == 0

    # the insertion is reversible back to the untouched base doc
    restored, _ = apply_operation(new_doc, inverse)
    assert content_hash(restored) == content_hash(doc)
