"""End-to-end coverage for `examples/assets/engineer/engineer.yaml`.

Drives the real pipeline directly (load -> resolve -> render -> validate),
the same path `pixel_forge.api` will wire up, without importing `api` itself
(that module is being written concurrently by another agent).
"""

from __future__ import annotations

from pathlib import Path

from pixel_forge.animation.resolver import resolve_frames
from pixel_forge.domain.loader import load_asset_doc
from pixel_forge.domain.palette import resolve_palette
from pixel_forge.rendering.canvas import Canvas
from pixel_forge.rendering.local import render_asset_frames
from pixel_forge.schemas import CharacterAsset
from pixel_forge.validation.engine import RuleContext, run_validation

ASSET_PATH = (
    Path(__file__).resolve().parents[2] / "examples" / "assets" / "engineer" / "engineer.yaml"
)


def _load() -> CharacterAsset:
    doc = load_asset_doc(ASSET_PATH)
    assert isinstance(doc, CharacterAsset)
    return doc


def test_frame_count_matches_directions_times_animation_frames() -> None:
    doc = _load()
    frames = render_asset_frames(doc)
    expected = sum(len(anim.frames) for anim in doc.animations.values()) * len(doc.directions)
    assert expected == 48  # idle(4) + walk(4) + attack(4), x 4 directions
    assert len(frames) == expected


def test_east_is_a_genuine_mirror_of_west() -> None:
    doc = _load()
    frames = render_asset_frames(doc)
    checked = 0
    for (animation, direction, index), west_canvas in frames.items():
        if direction != "west":
            continue
        east_canvas = frames[(animation, "east", index)]
        assert east_canvas.equals(west_canvas.mirror_x()), (
            f"east/{animation}/{index} is not a mirror of west/{animation}/{index}"
        )
        checked += 1
    assert checked == 12  # idle(4) + walk(4) + attack(4)


def test_every_frame_is_declared_canvas_size_and_non_empty() -> None:
    doc = _load()
    frames = render_asset_frames(doc)
    width, height = doc.asset.canvas
    for key, canvas in frames.items():
        assert (canvas.width, canvas.height) == (width, height), key
        assert canvas.bbox() is not None, f"frame {key} is empty"


def test_every_frame_uses_only_palette_colors() -> None:
    doc = _load()
    frames = render_asset_frames(doc)
    palette = resolve_palette(doc.palette)
    for key, canvas in frames.items():
        for rgba in canvas.colors():
            assert palette.contains_rgba(rgba), f"frame {key} uses non-palette colour {rgba}"


def test_rendering_twice_is_byte_identical() -> None:
    doc = _load()
    first = render_asset_frames(doc)
    second = render_asset_frames(doc)
    assert list(first.keys()) == list(second.keys())
    for key in first:
        assert first[key].equals(second[key]), f"frame {key} differs between renders"


def test_validation_report_has_no_blocking_findings() -> None:
    doc = _load()
    frames = render_asset_frames(doc)
    palette = resolve_palette(doc.palette)
    resolved = resolve_frames(doc)
    ctx = RuleContext(doc=doc, palette=palette, frames=frames, resolved=resolved, tiles={})
    report = run_validation(ctx)

    if report.blocking:
        details = "\n".join(
            f"  {f.severity.upper()} {f.rule_id}: {f.message}"
            for f in report.findings
            if f.severity == "error"
        )
        raise AssertionError(f"blocking validation findings for {doc.asset.id}:\n{details}")
    assert report.blocking is False


def test_helmet_and_weapon_regions_reference_declared_anchors() -> None:
    # Sanity check on the hand-authored schema features the plan calls out:
    # anchors, mirror, and direction_overrides are all exercised.
    doc = _load()
    assert doc.anchors["feet"] == (32, 57)
    assert "head" in doc.anchors
    assert "right_hand" in doc.anchors
    assert "upper_back" in doc.anchors
    assert doc.mirror == {"east": "west"}
    assert doc.direction_overrides["north"]["weapon"].visible is False
    assert doc.direction_overrides["north"]["backpack"].visible is True


def test_canvas_type_is_canvas() -> None:
    doc = _load()
    frames = render_asset_frames(doc)
    sample = next(iter(frames.values()))
    assert isinstance(sample, Canvas)
