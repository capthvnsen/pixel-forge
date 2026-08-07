"""Ramp generation, outline derivation, palette expansion, curated palettes, and
the palette-discipline rules PIX013/PIX014/PIX015.

Every rule has two tests (fires on a constructed offender / stays silent on a
clean doc) per AGENTS.md.
"""

from __future__ import annotations

import itertools
from typing import Any

import pytest
from pydantic import ValidationError

from pixel_forge.animation.resolver import resolve_frames
from pixel_forge.domain.palette import (
    build_ramp,
    cielab_lightness,
    darken,
    derive_outline,
    expand_palette,
    hex_to_rgba,
    lighten,
    palette_for_polish,
    relative_luminance,
    resolve_palette,
    rgb_to_hsl,
)
from pixel_forge.errors import PaletteError
from pixel_forge.references.curated import curated_palette_names, load_curated_palette
from pixel_forge.schemas import CharacterAsset, Palette, PaletteColor, parse_asset_doc
from pixel_forge.validation.engine import RuleContext, run_validation

# A saturated mid-green: hue ~98deg, HSL lightness ~0.40, a good stand-in for a
# material base colour across the ramp tests.
GRASS_BASE = "#5d8f3c"


# --- build_ramp ---------------------------------------------------------------------


def _lightness(hex_str: str) -> float:
    return rgb_to_hsl(hex_to_rgba(hex_str)[:3])[2]


def _hue(hex_str: str) -> float:
    return rgb_to_hsl(hex_to_rgba(hex_str)[:3])[0]


def test_build_ramp_has_exactly_n_steps() -> None:
    for steps in (1, 2, 3, 5, 7):
        assert len(build_ramp(GRASS_BASE, steps)) == steps


def test_build_ramp_is_monotonic_darkest_first() -> None:
    ramp = build_ramp(GRASS_BASE, 5)
    lightness = [_lightness(h) for h in ramp]
    assert lightness == sorted(lightness)
    assert lightness[0] < lightness[-1]
    # Adjacent steps must also be clearly distinct for a professional ramp.
    assert all(b > a for a, b in itertools.pairwise(lightness))


def test_build_ramp_preserves_hue_within_tolerance() -> None:
    base_hue = _hue(GRASS_BASE)
    for steps in (3, 5, 7):
        for hex_str in build_ramp(GRASS_BASE, steps):
            delta = abs(_hue(hex_str) - base_hue)
            assert delta <= 20.0, f"step {hex_str} drifted {delta:.1f}deg from base hue"


def test_build_ramp_base_hex_sits_at_middle_step() -> None:
    for steps, base_index in ((3, 1), (5, 2), (7, 3)):
        ramp = build_ramp(GRASS_BASE, steps)
        assert ramp[base_index] == GRASS_BASE.lower()


def test_build_ramp_is_deterministic() -> None:
    assert build_ramp(GRASS_BASE, 5) == build_ramp(GRASS_BASE, 5)
    assert build_ramp("#7a5230", 3) == build_ramp("#7a5230", 3)


def test_build_ramp_rejects_out_of_range_steps() -> None:
    with pytest.raises(PaletteError):
        build_ramp(GRASS_BASE, 0)
    with pytest.raises(PaletteError):
        build_ramp(GRASS_BASE, 8)


def test_build_ramp_preserves_alpha() -> None:
    ramp = build_ramp("#5d8f3c80", 3)
    assert all(hex_to_rgba(h)[3] == 0x80 for h in ramp)


def test_build_ramp_even_perceptual_spread() -> None:
    # The critic's measured reference ramps: per-step ΔL* roughly even (16-23),
    # light tone ≈ L* 60-75 for mid-lightness bases — not shadows crushed toward
    # the base while highlights blow out to L* 88-93.
    for base in ("#247a7f", "#3a5a78"):
        ramp = build_ramp(base, 3)
        ls = [cielab_lightness(hex_to_rgba(h)) for h in ramp]
        deltas = [b - a for a, b in itertools.pairwise(ls)]
        assert all(16.0 <= d <= 25.0 for d in deltas), (base, deltas)
        assert max(deltas) / min(deltas) <= 1.5, (base, deltas)
        assert 60.0 <= ls[-1] <= 75.0, (base, ls[-1])
        assert ls == sorted(ls)  # still darkest-first


def test_build_ramp_dark_base_shadow_does_not_band() -> None:
    # Regression: build_ramp("#0f0f0f", 3) used to emit a shadow step only
    # ΔL* 1.29 from the base, tripping the engine's own PIX014 banding rule.
    ramp = build_ramp("#0f0f0f", 3)
    ls = [cielab_lightness(hex_to_rgba(h)) for h in ramp]
    deltas = [b - a for a, b in itertools.pairwise(ls)]
    assert all(d >= 4.0 for d in deltas), deltas

    # And PIX014 must be fully silent on the ramp — not just banding-clean. The
    # shadow step is floor-clamped at L* 0 (no representable lightness below
    # it), so the asymmetry branch must exempt near-black ramps instead of
    # reporting a 5.4x 'lopsided' ratio that measures the clamp, not the ramp.
    palette = [
        {"id": "ink_shadow", "hex": ramp[0], "ramp": "ink", "role": "shadow"},
        {"id": "ink_mid", "hex": ramp[1], "ramp": "ink"},
        {"id": "ink_light", "hex": ramp[2], "ramp": "ink", "role": "light"},
    ]
    report = run_validation(_ctx(_doc(palette)), only=["PIX014"])
    assert report.findings == []


def test_build_ramp_warm_highlight_cool_shadow() -> None:
    # A cool blue-grey base: the light step must nudge warmer (toward
    # magenta/red, i.e. hue increasing) and the shadow cooler (toward blue) —
    # the direction the old shortest-arc-to-yellow shift got backwards.
    base_hue = _hue("#3a5a78")
    ramp = build_ramp("#3a5a78", 3)
    assert _hue(ramp[2]) >= base_hue, "highlight drifted cooler than the base"
    assert _hue(ramp[0]) <= base_hue, "shadow drifted warmer than the base"


# --- material (terrain) hue discipline: warm-light / cool-shadow -----------------------
#
# The round-2 critic measured the demo palette's auto-ramp as "value-only": the
# legacy ±6° hue whisper is imperceptible. `build_ramp(material_hue=True)` — the
# mode explicit-auto_ramp palettes get through `expand_palette`/`palette_for_polish`
# — applies a clearly-visible warm/cool split: light greens toward yellow-green,
# shadows toward blue-green, browns' lights toward orange, their shadows deep
# red-brown. The legacy default must stay byte-identical (sprite gate).


def test_build_ramp_material_hue_greens_warm_light_cool_shadow() -> None:
    base_hue = _hue("#4c9a2a")
    ramp = build_ramp("#4c9a2a", 3, material_hue=True)
    # Light -> yellow-green (hue down), shadow -> blue-green (hue up), clearly
    # visible (>= 8deg), never leaving the green hue family.
    assert base_hue - _hue(ramp[2]) >= 8.0, (base_hue, _hue(ramp[2]))
    assert _hue(ramp[0]) - base_hue >= 8.0, (base_hue, _hue(ramp[0]))
    assert 60.0 <= _hue(ramp[0]) <= 180.0 and 60.0 <= _hue(ramp[2]) <= 180.0


def test_build_ramp_material_hue_browns_warm_toward_orange() -> None:
    base_hue = _hue("#8a5a34")
    ramp = build_ramp("#8a5a34", 3, material_hue=True)
    # Light -> orange (hue up toward yellow), shadow -> deep red-brown (hue
    # down toward red): the reference dirt treatment, not the legacy pole rule
    # that sent both the long way around through yellow.
    assert _hue(ramp[2]) - base_hue >= 8.0, (base_hue, _hue(ramp[2]))
    assert base_hue - _hue(ramp[0]) >= 8.0, (base_hue, _hue(ramp[0]))
    assert 0.0 <= _hue(ramp[0]) < 60.0 and 0.0 <= _hue(ramp[2]) < 60.0


def test_build_ramp_material_hue_cool_hues_keep_p2_direction() -> None:
    # Blue-grey: material mode keeps the P2 direction (light up toward
    # magenta/red, shadow down toward blue) — only the magnitude grows.
    base_hue = _hue("#3a5a78")
    ramp = build_ramp("#3a5a78", 3, material_hue=True)
    assert _hue(ramp[2]) >= base_hue
    assert _hue(ramp[0]) <= base_hue


def test_build_ramp_material_hue_stays_in_family_and_deterministic() -> None:
    for base in ("#4c9a2a", "#8a5a34", "#2a6f97"):
        ramp = build_ramp(base, 3, material_hue=True)
        assert abs(_hue(ramp[0]) - _hue(base)) <= 20.0
        assert abs(_hue(ramp[2]) - _hue(base)) <= 20.0
        assert build_ramp(base, 3, material_hue=True) == ramp
    # Base hex preserved verbatim in material mode too.
    assert build_ramp("#4c9a2a", 3, material_hue=True)[1] == "#4c9a2a"


def test_build_ramp_default_is_legacy_byte_identical() -> None:
    # Pinned regression: the legacy default output is byte-identical to the
    # round-2 values (the sprite-render gate depends on this).
    assert build_ramp("#4c9a2a", 3) == ["#265b19", "#4c9a2a", "#99d76e"]
    assert build_ramp("#8a5a34", 3) == ["#3e2d17", "#8a5a34", "#cc9376"]
    assert build_ramp("#3a5a78", 3) == ["#17262f", "#3a5a78", "#7593bc"]


def test_expand_palette_material_hue_follows_explicit_auto_ramp() -> None:
    # A palette that explicitly declares auto_ramp gets the material tones…
    declared = Palette(
        id="p",
        colors=[PaletteColor(id="grass", hex="#4c9a2a")],
        auto_ramp=True,
    )
    expanded = expand_palette(declared)
    assert expanded.by_id["grass_shadow"].hex == build_ramp("#4c9a2a", 3, material_hue=True)[0]
    assert expanded.by_id["grass_light"].hex == build_ramp("#4c9a2a", 3, material_hue=True)[2]
    # …while a palette that only receives auto_ramp *forced* by the polish
    # pipeline keeps the legacy whisper tones.
    flat = Palette(id="p", colors=[PaletteColor(id="grass", hex="#4c9a2a")])
    polished = palette_for_polish(flat)
    assert polished.by_id["grass_shadow"].hex == build_ramp("#4c9a2a", 3)[0]
    assert polished.by_id["grass_light"].hex == build_ramp("#4c9a2a", 3)[2]
    # And palette_for_polish on the explicit palette also yields material tones.
    polished_declared = palette_for_polish(declared)
    assert polished_declared.by_id["grass_shadow"].hex == build_ramp(
        "#4c9a2a", 3, material_hue=True
    )[0]


# --- derive_outline ----------------------------------------------------------------


def test_derive_outline_is_dark_charcoal_never_black() -> None:
    for base in (GRASS_BASE, "#7a5230", "#3f6fa8", "#8f9aa5"):
        outline = derive_outline(base)
        assert _lightness(outline) <= 0.16
        assert hex_to_rgba(outline)[:3] != (0, 0, 0)


def test_derive_outline_is_hue_tinted() -> None:
    outline = derive_outline(GRASS_BASE)
    # Round-trip through 8-bit channels can nudge the hue a degree or two at
    # charcoal lightness; the tint must stay recognisably the base's hue.
    assert abs(_hue(outline) - _hue(GRASS_BASE)) <= 3.0
    r, g, b, _ = hex_to_rgba(outline)
    assert max(r, g, b) - min(r, g, b) > 0  # a hint of hue, not neutral grey


def test_derive_outline_is_deterministic() -> None:
    assert derive_outline(GRASS_BASE) == derive_outline(GRASS_BASE)


# --- lighten / darken --------------------------------------------------------------


def test_lighten_darken_move_lightness_and_clamp() -> None:
    base = GRASS_BASE
    lighter = lighten(base, 0.2)
    darker = darken(base, 0.2)
    assert _lightness(lighter) > _lightness(base)
    assert _lightness(darker) < _lightness(base)
    # Hue and saturation are preserved, only value moves.
    assert abs(_hue(lighter) - _hue(base)) <= 1.0
    assert abs(_hue(darker) - _hue(base)) <= 1.0
    # Clamping: a huge lighten cannot blow past white, darken past black.
    assert _lightness(lighten("#ffffff", 1.0)) == 1.0
    assert _lightness(darken("#000000", 1.0)) == 0.0


def test_lighten_darken_reject_out_of_range_amounts() -> None:
    with pytest.raises(PaletteError):
        lighten(GRASS_BASE, 1.5)
    with pytest.raises(PaletteError):
        darken(GRASS_BASE, -0.1)


# --- luminance helpers -------------------------------------------------------------


def test_relative_luminance_and_cielab_lightness_bounds() -> None:
    assert relative_luminance((0, 0, 0, 255)) == 0.0
    assert relative_luminance((255, 255, 255, 255)) == 1.0
    assert cielab_lightness((0, 0, 0, 255)) == 0.0
    assert cielab_lightness((255, 255, 255, 255)) == 100.0
    mid = cielab_lightness(hex_to_rgba(GRASS_BASE))
    assert 0.0 < mid < 100.0


# --- schema: ramp config fields (backward compatible) ------------------------------


def test_palette_color_ramp_steps_parses_and_validates_bounds() -> None:
    assert PaletteColor(id="a", hex="#ff0000").ramp_steps == 3
    assert PaletteColor(id="a", hex="#ff0000", ramp_steps=5).ramp_steps == 5
    with pytest.raises(ValidationError):
        PaletteColor(id="a", hex="#ff0000", ramp_steps=0)
    with pytest.raises(ValidationError):
        PaletteColor(id="a", hex="#ff0000", ramp_steps=8)


def test_palette_auto_ramp_and_derive_outline_parse() -> None:
    palette = Palette(id="p", colors=[], auto_ramp=True, derive_outline=True)
    assert palette.auto_ramp is True
    assert palette.derive_outline is True


def test_existing_spec_without_ramp_fields_resolves_exactly_as_before() -> None:
    # A spec written before ramp fields existed must parse with the new defaults
    # and resolve identically: auto_ramp/derive_outline off, ramp_steps 3.
    data = {
        "schema_version": 1,
        "asset": {"id": "hero", "type": "character", "canvas": [8, 8]},
        "palette": {"id": "p", "colors": [{"id": "red", "hex": "#ff0000"}]},
        "directions": ["south"],
        "anchors": {"root": [0, 0]},
        "regions": {"body": {"anchor": "root", "layer": 0, "shapes": []}},
        "animations": {
            "idle": {"loop": True, "frames": [{"duration_ms": 100, "events": [], "transforms": {}}]}
        },
        "export": {},
        "validation": {},
    }
    doc = parse_asset_doc(data)
    assert isinstance(doc, CharacterAsset)
    assert doc.palette.auto_ramp is False
    assert doc.palette.derive_outline is False
    assert doc.palette.colors[0].ramp_steps == 3
    assert resolve_palette(doc.palette).rgba("red") == (255, 0, 0, 255)


# --- expand_palette ----------------------------------------------------------------


def test_expand_palette_is_noop_without_flags() -> None:
    palette = Palette(id="p", colors=[PaletteColor(id="a", hex="#ff0000")])
    assert expand_palette(palette) is palette


def test_expand_palette_auto_ramp_three_steps() -> None:
    palette = Palette(
        id="p",
        colors=[PaletteColor(id="armor", hex="#4a5c7d")],
        auto_ramp=True,
    )
    expanded = expand_palette(palette)
    ids = [c.id for c in expanded.colors]
    assert ids == ["armor_shadow", "armor", "armor_light"]
    by_id = expanded.by_id
    assert by_id["armor"].hex == "#4a5c7d"  # base preserved verbatim
    assert by_id["armor_shadow"].role == "shadow"
    assert by_id["armor_light"].role == "light"
    # All three steps group under one ramp material for PIX012/PIX014.
    assert by_id["armor_shadow"].ramp == "armor"
    assert by_id["armor_light"].ramp == "armor"
    assert all(c.ramp_steps == 1 for c in expanded.colors if c.id != "armor")


def test_expand_palette_auto_ramp_five_steps_names() -> None:
    palette = Palette(
        id="p",
        colors=[PaletteColor(id="metal", hex="#6e7683", ramp_steps=5)],
        auto_ramp=True,
    )
    ids = [c.id for c in expand_palette(palette).colors]
    assert ids == ["metal_shadow", "metal_dark", "metal", "metal_light", "metal_bright"]


def test_expand_palette_auto_ramp_skips_ramp_steps_one() -> None:
    palette = Palette(
        id="p",
        colors=[
            PaletteColor(id="flat", hex="#ff0000", ramp_steps=1),
            PaletteColor(id="tonal", hex="#00ff00"),
        ],
        auto_ramp=True,
    )
    expanded = expand_palette(palette)
    assert expanded.by_id["flat"].hex == "#ff0000"
    assert "flat_shadow" not in expanded.by_id
    assert "tonal_shadow" in expanded.by_id


def test_expand_palette_is_idempotent() -> None:
    palette = Palette(
        id="p",
        colors=[
            PaletteColor(id="armor", hex="#4a5c7d"),
            PaletteColor(id="wood", hex="#7a5230", ramp_steps=5),
        ],
        auto_ramp=True,
        derive_outline=True,
    )
    once = expand_palette(palette)
    twice = expand_palette(once)
    assert [(c.id, c.hex) for c in twice.colors] == [(c.id, c.hex) for c in once.colors]


def test_expand_palette_derive_outline_adds_tinted_outline() -> None:
    palette = Palette(
        id="p",
        colors=[
            PaletteColor(id="light", hex="#e0edd4"),
            PaletteColor(id="dark", hex="#3d682c"),
        ],
        derive_outline=True,
    )
    expanded = expand_palette(palette)
    outline = expanded.by_id["outline"]
    assert outline.role == "outline"
    assert outline.hex != "#000000"
    # Derived from the darkest colour, so it stays darker than everything else.
    assert relative_luminance(hex_to_rgba(outline.hex)) < relative_luminance(
        hex_to_rgba("#3d682c")
    )


def test_expand_palette_outline_base_skips_pure_black_shadow() -> None:
    # Regression for the render-polish outline bug: expand_palette derived the
    # outline from the palette's overall darkest declared colour, and every
    # example palette declares `shadow: #000000`, so derive_outline(#000000)
    # produced neutral #0f0f0f with zero hue — a black outline instead of the
    # hue-tinted charcoal the module documents. The base must be the darkest
    # *hue-bearing* declared colour (suit here), so the outline keeps the
    # suit's blue tint.
    palette = Palette(
        id="p",
        colors=[
            PaletteColor(id="shadow", hex="#000000"),
            PaletteColor(id="suit", hex="#3a5a78"),
        ],
        derive_outline=True,
    )
    expanded = expand_palette(palette)
    outline = expanded.by_id["outline"]
    assert outline.role == "outline"
    # Derived from the hue-bearing suit, not the pure-black shadow.
    assert outline.hex == derive_outline("#3a5a78")
    assert outline.hex != "#0f0f0f"
    # Hue-tinted charcoal: not neutral grey, and the tint follows the suit's
    # blue family (hue ~209), not the shadow's zero hue.
    r, g, b, _ = hex_to_rgba(outline.hex)
    assert max(r, g, b) - min(r, g, b) > 0
    assert b >= r and b >= g
    # Still darker than the hue-bearing base it derives from.
    assert relative_luminance(hex_to_rgba(outline.hex)) < relative_luminance(
        hex_to_rgba("#3a5a78")
    )


def test_expand_palette_outline_base_all_near_black_falls_back_to_darkest() -> None:
    # Degenerate palette: every declared colour is near-black (no usable hue).
    # Expansion must still append an outline via the previous darkest-colour
    # behaviour — and it is still never pure black.
    palette = Palette(
        id="p",
        colors=[
            PaletteColor(id="a", hex="#000000"),
            PaletteColor(id="b", hex="#0a0a0a"),
        ],
        derive_outline=True,
    )
    expanded = expand_palette(palette)
    outline = expanded.by_id["outline"]
    assert outline.role == "outline"
    assert hex_to_rgba(outline.hex)[:3] != (0, 0, 0)


def test_expand_palette_derive_outline_respects_existing_outline() -> None:
    palette = Palette(
        id="p",
        colors=[
            PaletteColor(id="a", hex="#3d682c"),
            PaletteColor(id="outline", hex="#101010", role="outline"),
        ],
        derive_outline=True,
    )
    expanded = expand_palette(palette)
    assert expanded.by_id["outline"].hex == "#101010"
    assert len(expanded.colors) == 2


def test_expand_palette_raises_on_generated_id_collision() -> None:
    palette = Palette(
        id="p",
        colors=[
            PaletteColor(id="armor", hex="#4a5c7d"),
            PaletteColor(id="armor_shadow", hex="#123456"),
        ],
        auto_ramp=True,
    )
    with pytest.raises(PaletteError, match="armor_shadow"):
        expand_palette(palette)


# --- palette_for_polish --------------------------------------------------------


def test_palette_for_polish_expands_flat_palette_with_flags_forced_on() -> None:
    # The render-polish pass quantizes onto the palette, so a flat 2-colour
    # palette must gain ramp tones + a derived outline to quantize onto.
    palette = Palette(
        id="p",
        colors=[
            PaletteColor(id="armor", hex="#4a5c7d"),
            PaletteColor(id="flat", hex="#ff0000", ramp_steps=1),
        ],
    )
    polished = palette_for_polish(palette)
    ids = [c.id for c in polished.colors]
    assert ids == ["armor_shadow", "armor", "armor_light", "flat", "outline"]
    # Declared colours are preserved verbatim (ids and hexes).
    assert polished.by_id["armor"].hex == "#4a5c7d"
    assert polished.by_id["flat"].hex == "#ff0000"
    # ramp_steps=1 colours stay flat.
    assert "flat_shadow" not in polished.by_id
    # The derived outline exists and is never pure black.
    assert polished.by_id["outline"].role == "outline"
    assert hex_to_rgba(polished.by_id["outline"].hex)[:3] != (0, 0, 0)


def test_palette_for_polish_is_idempotent() -> None:
    palette = Palette(
        id="p",
        colors=[PaletteColor(id="armor", hex="#4a5c7d")],
        auto_ramp=True,
        derive_outline=True,
    )
    once = palette_for_polish(palette)
    twice = palette_for_polish(once)
    assert [(c.id, c.hex) for c in twice.colors] == [(c.id, c.hex) for c in once.colors]


def test_palette_for_polish_keeps_declared_outline() -> None:
    palette = Palette(
        id="p",
        colors=[
            PaletteColor(id="armor", hex="#4a5c7d"),
            PaletteColor(id="outline", hex="#101010", role="outline", ramp_steps=1),
        ],
    )
    polished = palette_for_polish(palette)
    assert polished.by_id["outline"].hex == "#101010"
    assert len([c for c in polished.colors if c.id == "outline"]) == 1


# --- curated palettes --------------------------------------------------------------


def test_curated_palettes_validate_and_meet_quality_bar() -> None:
    for name in curated_palette_names():
        palette = load_curated_palette(name)
        assert palette.id == name
        # Professional coherent set: 16-32 colours.
        assert 16 <= len(palette.colors) <= 32
        assert len(palette.by_id) == len(palette.colors)  # unique ids

        # Every non-accent material is exactly a 3-tone ramp with shadow/mid/light.
        by_ramp: dict[str, list[PaletteColor]] = {}
        for color in palette.colors:
            if color.ramp is not None:
                by_ramp.setdefault(color.ramp, []).append(color)
        for ramp, steps in by_ramp.items():
            if ramp == "accent":
                continue
            assert len(steps) == 3, f"{name}/{ramp} has {len(steps)} steps"
            # Adjacent steps are clearly separated (PIX014-clean) and balanced:
            # even ΔL* per step, no crushed shadows vs blown-out highlights.
            ordered = sorted(steps, key=lambda c: cielab_lightness(hex_to_rgba(c.hex)))
            deltas = [
                abs(
                    cielab_lightness(hex_to_rgba(a.hex))
                    - cielab_lightness(hex_to_rgba(b.hex))
                )
                for a, b in itertools.pairwise(ordered)
            ]
            assert all(d >= 4.0 for d in deltas), f"{name}/{ramp} banded: {deltas}"
            assert max(deltas) / min(deltas) <= 2.5, f"{name}/{ramp} lopsided: {deltas}"

        # The outline is hue-tinted charcoal, never pure black.
        outline = palette.by_id["outline"]
        assert outline.role == "outline"
        assert hex_to_rgba(outline.hex)[:3] != (0, 0, 0)
        assert _lightness(outline.hex) <= 0.16


def test_load_curated_palette_unknown_name_raises() -> None:
    with pytest.raises(PaletteError, match="unknown curated palette"):
        load_curated_palette("nope")


# --- PIX013/PIX014/PIX015 rule tests ------------------------------------------------


def _doc(palette_colors: list[dict[str, Any]]) -> CharacterAsset:
    data = {
        "schema_version": 1,
        "asset": {"id": "hero", "type": "character", "canvas": [8, 8]},
        "palette": {"id": "p", "colors": palette_colors},
        "directions": ["south"],
        "anchors": {"root": [0, 0]},
        "regions": {"body": {"anchor": "root", "layer": 0, "shapes": []}},
        "animations": {
            "idle": {"loop": True, "frames": [{"duration_ms": 100, "events": [], "transforms": {}}]}
        },
        "export": {},
        "validation": {},
    }
    doc = parse_asset_doc(data)
    assert isinstance(doc, CharacterAsset)
    return doc


def _ctx(doc: CharacterAsset) -> RuleContext:
    return RuleContext(
        doc=doc,
        palette=resolve_palette(doc.palette),
        frames={},
        resolved=resolve_frames(doc),
        tiles={},
    )


_TONAL_PALETTE = [
    {"id": "armor_shadow", "hex": "#2b3648", "ramp": "armor", "role": "shadow"},
    {"id": "armor_mid", "hex": "#4a5c7d", "ramp": "armor"},
    {"id": "armor_light", "hex": "#7b93b8", "ramp": "armor", "role": "light"},
    {"id": "ink", "hex": "#12141c", "role": "outline"},
]


def test_pix013_fires_on_flat_palette() -> None:
    flat = [
        {"id": "a", "hex": "#7a7a7a"},
        {"id": "b", "hex": "#808080"},
        {"id": "c", "hex": "#868686"},
    ]
    report = run_validation(_ctx(_doc(flat)), only=["PIX013"])
    assert len(report.findings) == 1
    assert report.findings[0].rule_id == "PIX013"
    assert report.findings[0].severity == "warning"
    span = report.findings[0].measurements["lightness_span"]
    assert isinstance(span, (int, float)) and span < 0.25


def test_pix013_does_not_fire_on_tonal_palette() -> None:
    report = run_validation(_ctx(_doc(_TONAL_PALETTE)), only=["PIX013"])
    assert report.findings == []


def test_pix013_ignores_outline_colours_when_measuring_spread() -> None:
    # A palette of flat mids plus a dark outline is still unshadeable; the
    # outline must not mask the flatness.
    palette = [
        {"id": "a", "hex": "#7a7a7a"},
        {"id": "b", "hex": "#808080"},
        {"id": "ink", "hex": "#12141c", "role": "outline"},
    ]
    report = run_validation(_ctx(_doc(palette)), only=["PIX013"])
    assert len(report.findings) == 1


def test_pix014_fires_on_banded_ramp() -> None:
    banded = [
        {"id": "metal_shadow", "hex": "#4a4a50", "ramp": "metal", "role": "shadow"},
        {"id": "metal_mid", "hex": "#4b4b51", "ramp": "metal"},
        {"id": "metal_light", "hex": "#a0a0b0", "ramp": "metal", "role": "light"},
    ]
    report = run_validation(_ctx(_doc(banded)), only=["PIX014"])
    # The banding finding (a < 4 L* gap) fires; the same ramp also trips the
    # balance check, so look for the banding finding specifically.
    banding = [f for f in report.findings if f.measurements.get("delta_l") is not None]
    assert len(banding) == 1
    finding = banding[0]
    assert finding.rule_id == "PIX014"
    assert finding.measurements["ramp"] == "metal"
    delta_l = finding.measurements["delta_l"]
    assert isinstance(delta_l, (int, float)) and delta_l < 4.0


def test_pix014_does_not_fire_on_well_spaced_ramp() -> None:
    # build_ramp output: shadow/mid/light separated by > 4 CIE L* by design.
    palette = [
        {"id": "metal_shadow", "hex": build_ramp("#6e7683", 3)[0], "ramp": "metal"},
        {"id": "metal_mid", "hex": build_ramp("#6e7683", 3)[1], "ramp": "metal"},
        {"id": "metal_light", "hex": build_ramp("#6e7683", 3)[2], "ramp": "metal"},
    ]
    report = run_validation(_ctx(_doc(palette)), only=["PIX014"])
    assert report.findings == []


def test_pix014_fires_on_lopsided_ramp() -> None:
    # Every adjacent gap clears the 4 L* minimum, but the ramp is 4x lopsided
    # (shadows crushed at ΔL* ~15, highlights blown out at ΔL* ~45) — the
    # build_ramp asymmetry the critic measured.
    lopsided = [
        {"id": "teal_shadow", "hex": "#1a525c", "ramp": "teal", "role": "shadow"},
        {"id": "teal_mid", "hex": "#247a7f", "ramp": "teal"},
        {"id": "teal_light", "hex": "#c9f0ee", "ramp": "teal", "role": "light"},
    ]
    report = run_validation(_ctx(_doc(lopsided)), only=["PIX014"])
    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.rule_id == "PIX014"
    assert finding.measurements["ramp"] == "teal"
    ratio = finding.measurements["asymmetry_ratio"]
    assert isinstance(ratio, (int, float)) and ratio > 2.5


def test_pix014_does_not_fire_on_balanced_ramp() -> None:
    # Even build_ramp output: ΔL* ~23 per step, asymmetry ratio ~1.
    palette = [
        {"id": "teal_shadow", "hex": build_ramp("#247a7f", 3)[0], "ramp": "teal"},
        {"id": "teal_mid", "hex": build_ramp("#247a7f", 3)[1], "ramp": "teal"},
        {"id": "teal_light", "hex": build_ramp("#247a7f", 3)[2], "ramp": "teal"},
    ]
    report = run_validation(_ctx(_doc(palette)), only=["PIX014"])
    assert report.findings == []


def test_pix014_judges_declared_palette_not_expanded_render_palette() -> None:
    # The engine's validation path hands rules the palette_for_polish-EXPANDED
    # palette: every declared colour defaults to ramp_steps 3, so a hand-authored
    # three-step ramp arrives as nine mixed hand-authored + build_ramp-derived
    # tones with ΔL* gaps down to ~1.6 and asymmetry ratios up to ~313x — exactly
    # the vanguard armour/metal false positives the round-2 critic measured.
    # PIX014 must judge the *declared* steps (matching PIX012's choice), so these
    # textbook-good ramps stay silent even under the real expanded context.
    palette = [
        {"id": "armor_lite", "hex": "#7b93b8", "ramp": "armor", "role": "light"},
        {"id": "armor_mid", "hex": "#4a5c7d", "ramp": "armor"},
        {"id": "armor_dark", "hex": "#2b3648", "ramp": "armor", "role": "shadow"},
        {"id": "metal_lite", "hex": "#a8b1bf", "ramp": "metal", "role": "light"},
        {"id": "metal_mid", "hex": "#6e7683", "ramp": "metal"},
        {"id": "metal_dark", "hex": "#3a3f47", "ramp": "metal", "role": "shadow"},
    ]
    doc = _doc(palette)
    expanded_ctx = RuleContext(
        doc=doc,
        palette=resolve_palette(palette_for_polish(doc.palette)),
        frames={},
        resolved=resolve_frames(doc),
        tiles={},
    )
    report = run_validation(expanded_ctx, only=["PIX014"])
    assert report.findings == []

    # Expansion noise must not mask a real authoring problem either: a genuinely
    # lopsided declared ramp still fires through the same expanded context.
    lopsided = [
        {"id": "teal_shadow", "hex": "#1a525c", "ramp": "teal", "role": "shadow"},
        {"id": "teal_mid", "hex": "#247a7f", "ramp": "teal"},
        {"id": "teal_light", "hex": "#c9f0ee", "ramp": "teal", "role": "light"},
    ]
    lopsided_doc = _doc(lopsided)
    lopsided_ctx = RuleContext(
        doc=lopsided_doc,
        palette=resolve_palette(palette_for_polish(lopsided_doc.palette)),
        frames={},
        resolved=resolve_frames(lopsided_doc),
        tiles={},
    )
    lopsided_report = run_validation(lopsided_ctx, only=["PIX014"])
    assert len(lopsided_report.findings) == 1
    assert lopsided_report.findings[0].measurements["ramp"] == "teal"


def test_pix015_fires_on_pure_black_outline() -> None:
    palette = [
        {"id": "a", "hex": "#7a7a7a"},
        {"id": "outline", "hex": "#000000", "role": "outline"},
    ]
    report = run_validation(_ctx(_doc(palette)), only=["PIX015"])
    assert len(report.findings) == 1
    assert report.findings[0].rule_id == "PIX015"
    assert report.findings[0].measurements["color_id"] == "outline"
    assert report.findings[0].measurements["color_hex"] == "#000000"


def test_pix015_does_not_fire_on_tinted_outline_or_black_fill() -> None:
    palette = [
        {"id": "a", "hex": "#7a7a7a"},
        {"id": "ink", "hex": "#14100f", "role": "outline"},  # tinted charcoal: fine
        {"id": "black", "hex": "#000000"},  # pure black fill, not an outline: fine
    ]
    report = run_validation(_ctx(_doc(palette)), only=["PIX015"])
    assert report.findings == []
