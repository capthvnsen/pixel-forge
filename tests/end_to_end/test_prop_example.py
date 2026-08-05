"""End-to-end proof that examples/assets/beacon/beacon.yaml renders and validates
clean, and that it actually exercises layered transform animation, a genuine
blink (not just a colour swap), and a surviving procedural shader block."""

from __future__ import annotations

from pathlib import Path

from pixel_forge.animation.resolver import resolve_frames
from pixel_forge.domain.loader import load_asset_doc
from pixel_forge.domain.palette import resolve_palette
from pixel_forge.rendering.local import render_asset_frames
from pixel_forge.schemas import PropAsset
from pixel_forge.validation.engine import RuleContext, run_validation

ASSET_PATH = Path(__file__).resolve().parents[2] / "examples" / "assets" / "beacon" / "beacon.yaml"

# The "base" region's own shapes only ever occupy this sub-rectangle (see the
# layout note at the top of beacon.yaml); no other region's pixels ever land here.
_BASE_BOX = (10, 10, 22, 30)  # x0, y0, x1, y1

# The "vane" region's crossbar+stem, swept across every offset any frame applies.
_VANE_BOX = (11, 6, 22, 10)

# The "lamp" region's footprint (never offset, so this box is fixed).
_LAMP_BOX = (15, 1, 18, 4)


def _load() -> PropAsset:
    doc = load_asset_doc(ASSET_PATH)
    assert isinstance(doc, PropAsset)
    return doc


def test_static_base_region_is_identical_across_every_frame() -> None:
    doc = _load()
    frames = render_asset_frames(doc)
    assert len(frames) > 1

    x0, y0, x1, y1 = _BASE_BOX
    canvases = list(frames.values())
    reference = canvases[0].array[y0:y1, x0:x1]
    for canvas in canvases[1:]:
        sub = canvas.array[y0:y1, x0:x1]
        assert (sub == reference).all(), "static base region drifted across frames"


def test_vane_region_pixels_change_across_frames() -> None:
    doc = _load()
    frames = render_asset_frames(doc)

    x0, y0, x1, y1 = _VANE_BOX
    subs = [canvas.array[y0:y1, x0:x1] for canvas in frames.values()]
    assert any((subs[0] != sub).any() for sub in subs[1:]), (
        "vane region never changed -- layered transform animation isn't doing anything"
    )


def test_lamp_blinks_present_in_some_frames_absent_in_others() -> None:
    doc = _load()
    frames = render_asset_frames(doc)

    x0, y0, x1, y1 = _LAMP_BOX
    presence = []
    for canvas in frames.values():
        sub = canvas.array[y0:y1, x0:x1]
        presence.append(bool((sub[..., 3] != 0).any()))
    assert any(presence), "lamp is never rendered in any frame"
    assert not all(presence), "lamp never turns off; the blink toggle isn't exercised"


def test_procedural_block_survives_parsing() -> None:
    doc = _load()
    procedural = doc.animations["active"].procedural
    assert procedural is not None
    assert procedural.shader == "energy_pulse"
    assert procedural.target_region == "glow"
    assert procedural.params == {
        "frequency_hz": 6.0,
        "intensity": 0.8,
        "base_color": "glow_dim",
    }


def test_validation_report_has_no_blocking_findings() -> None:
    doc = _load()
    frames = render_asset_frames(doc)
    resolved = resolve_frames(doc)
    palette = resolve_palette(doc.palette)
    ctx = RuleContext(doc=doc, palette=palette, frames=frames, resolved=resolved, tiles={})
    report = run_validation(ctx)
    if report.blocking:
        print(report.to_text())
    assert report.blocking is False


def test_render_is_deterministic() -> None:
    doc = _load()
    frames_a = render_asset_frames(doc)
    frames_b = render_asset_frames(doc)
    assert frames_a.keys() == frames_b.keys()
    for key in frames_a:
        assert frames_a[key].equals(frames_b[key])
