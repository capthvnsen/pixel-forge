"""End-to-end coverage for `examples/enemy/crawler.yaml`.

Drives the real pipeline directly (load -> resolve -> render -> validate),
the same path `pixel_forge.api` will wire up, without importing `api` itself
(that module is being written concurrently by another agent).
"""

from __future__ import annotations

from pathlib import Path

from pixel_forge.animation.resolver import resolve_frames
from pixel_forge.domain.loader import load_asset_doc
from pixel_forge.domain.palette import resolve_palette
from pixel_forge.rendering.local import render_asset_frames
from pixel_forge.schemas import EnemyAsset
from pixel_forge.validation.engine import RuleContext, run_validation

ASSET_PATH = Path(__file__).resolve().parents[2] / "examples" / "enemy" / "crawler.yaml"


def _load() -> EnemyAsset:
    doc = load_asset_doc(ASSET_PATH)
    assert isinstance(doc, EnemyAsset)
    return doc


def test_frame_count_matches_directions_times_animation_frames() -> None:
    doc = _load()
    frames = render_asset_frames(doc)
    expected = sum(len(anim.frames) for anim in doc.animations.values()) * len(doc.directions)
    # idle(2) + move(4) + telegraph(2) + attack(4) + impact(2) + death(4) = 18, x 2 directions
    assert expected == 36
    assert len(frames) == expected


def test_west_is_a_genuine_mirror_of_east() -> None:
    doc = _load()
    assert doc.mirror == {"west": "east"}
    frames = render_asset_frames(doc)
    checked = 0
    for (animation, direction, index), east_canvas in frames.items():
        if direction != "east":
            continue
        west_canvas = frames[(animation, "west", index)]
        assert west_canvas.equals(east_canvas.mirror_x()), (
            f"west/{animation}/{index} is not a mirror of east/{animation}/{index}"
        )
        checked += 1
    assert checked == 18


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


def test_death_animation_does_not_loop() -> None:
    doc = _load()
    assert doc.animations["death"].loop is False


def test_combat_block_names_real_animations() -> None:
    doc = _load()
    assert doc.combat.telegraph_animation is not None
    assert doc.combat.telegraph_animation in doc.animations
    assert doc.combat.death_animation is not None
    assert doc.combat.death_animation in doc.animations
    for animation, frame_indices in doc.combat.hit_frames.items():
        assert animation in doc.animations
        frame_count = len(doc.animations[animation].frames)
        for index in frame_indices:
            assert 0 <= index < frame_count


def test_declared_events_land_on_the_expected_frames() -> None:
    doc = _load()
    telegraph_frames = doc.animations["telegraph"].frames
    assert "telegraph_start" in telegraph_frames[0].events

    attack_frames = doc.animations["attack"].frames
    on_frame, off_frame = doc.combat.hit_frames["attack"][0], doc.combat.hit_frames["attack"][0] + 1
    assert "hitbox_on" in attack_frames[on_frame].events
    assert "hitbox_off" in attack_frames[off_frame].events

    death_frames = doc.animations["death"].frames
    assert "death_complete" in death_frames[-1].events
